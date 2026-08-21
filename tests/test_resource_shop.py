import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mc_bot.accounts import MinecraftAccount
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import (
    LevelBotXpClient,
    MinecraftResourceExchangeEvent,
    MinecraftResourcePack,
    MinecraftResourceShop,
    MinecraftXpWallet,
)
from mc_bot.resource_shop import (
    EmeraldDiamondConfirmView,
    EmeraldDiamondPackSelectView,
    MinecraftResourceConfirmView,
    MinecraftResourcePackSelectView,
    MinecraftResourceShopPanelView,
    minecraft_resource_shop_embed,
    resource_exchange_actionbar_command,
    resource_exchange_tellraw_command,
    resource_give_command,
)


class ResourceRcon:
    def __init__(self, *, response: str = "Gave 3 [Diamond] to Steve") -> None:
        self.response = response
        self.exception: Exception | None = None
        self.actionbar_failures = 0
        self.tellraw_failures = 0
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("give Steve "):
            if self.exception is not None:
                raise self.exception
            return self.response
        if command.startswith("title Steve actionbar "):
            if self.actionbar_failures > 0:
                self.actionbar_failures -= 1
                raise OSError("actionbar response lost")
            return ""
        if command.startswith("tellraw @a "):
            if self.tellraw_failures > 0:
                self.tellraw_failures -= 1
                raise OSError("tellraw response lost")
            return ""
        raise AssertionError(f"unexpected RCON command: {command}")


def _event(*, status: str = "pending") -> MinecraftResourceExchangeEvent:
    return MinecraftResourceExchangeEvent(
        id=7,
        event_id="resource-exchange-7",
        guild_id=456,
        user_id=123,
        minecraft_account_id="mc-bot:1",
        item_id="minecraft:diamond",
        item_name="ダイヤモンド",
        item_count=3,
        cost_xp=550,
        status=status,
    )


def _bot(tmp_path) -> tuple[MinecraftDiscordBot, MinecraftAccount]:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
            level_bot_api_url="https://levels.example.test",
            level_bot_api_token="xp-secret",
        )
    )
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    return bot, account


def test_resource_panel_lists_server_rates_and_is_persistent() -> None:
    packs = (
        MinecraftResourcePack("minecraft:emerald", "エメラルド", 4, 100),
        MinecraftResourcePack("minecraft:emerald", "エメラルド", 16, 360),
        MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 3, 550),
    )
    embed = minecraft_resource_shop_embed(packs)

    async def build_views() -> tuple[
        MinecraftResourceShopPanelView, MinecraftResourcePackSelectView
    ]:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        shop = MinecraftResourceShop(MinecraftXpWallet(600, 0, 600), packs)
        return (
            MinecraftResourceShopPanelView(bot),
            MinecraftResourcePackSelectView(bot, owner_id=123, shop=shop),
        )

    panel, select = asyncio.run(build_views())

    assert embed.title == "Minecraft 資源交換所"
    assert "最大 **64個・1スタック**" in str(embed.description)
    assert "手持ちのエメラルドもダイヤモンドへ交換" in str(embed.description)
    assert "**サーバーXP → 資源**" in str(embed.fields[0].value)
    assert "`サーバーXP 100` → `エメラルド x4`" in str(embed.fields[0].value)
    assert "`サーバーXP 360` → `エメラルド x16`" in str(embed.fields[0].value)
    assert "**手持ち資源 → 資源**" in str(embed.fields[0].value)
    assert "`エメラルド x32` → `ダイヤモンド x1`" in str(embed.fields[0].value)
    assert "`エメラルド x64` → `ダイヤモンド x2`" in str(embed.fields[0].value)
    assert "`砂岩 x64` → `50 サーバーXP`" in str(embed.fields[0].value)
    assert "1人1日 **1,500 サーバーXP**" in str(embed.fields[0].value)
    assert "毎日0時・日本時間に更新" in str(embed.fields[0].value)
    assert "上限超過時は資材を回収しません" in str(embed.fields[0].value)
    assert "エメラルドにも交換" in str(embed.fields[0].value)
    assert embed.fields[1].name == "🎮 ゲーム内コマンド"
    assert "`/exchange`" in str(embed.fields[1].value)
    assert "`/exchange resource <diamond|emerald> <個数>`" in str(embed.fields[1].value)
    assert "`/exchange emerald-diamond <32|64>`" in str(embed.fields[1].value)
    assert "`/exchange buyback <1|2|4|8|16|max|all>`" in str(embed.fields[1].value)
    assert "`/exchange balance`" in str(embed.fields[1].value)
    assert "diamondは `1|3|8|16|32|64`" in str(embed.fields[1].value)
    assert "足元へドロップ" in str(embed.fields[2].value)
    assert embed.fields[3].name == "📢 交換完了時の通知"
    assert "**Discordのログチャンネル**" in str(embed.fields[3].value)
    assert "**Minecraft内チャット**" in str(embed.fields[3].value)
    assert "当日の残り買取枠" in str(embed.fields[3].value)
    assert "Minecraft内で本人だけ" in str(embed.fields[3].value)
    assert embed.footer.text == "残高・選択・確認画面は本人にのみ表示されます"
    assert panel.timeout is None
    assert [child.custom_id for child in panel.children] == [
        "mc-resource-shop:open",
        "mc-resource-shop:emerald-diamond",
        "mc-resource-shop:balance",
    ]
    assert [option.value for option in select.children[0].options] == ["0", "1", "2"]


def test_emerald_diamond_menu_exposes_only_fixed_safe_rates() -> None:
    async def build() -> EmeraldDiamondPackSelectView:
        return EmeraldDiamondPackSelectView(
            MinecraftDiscordBot(Config(discord_token="secret")), owner_id=123
        )

    view = asyncio.run(build())

    assert [option.value for option in view.children[0].options] == ["32", "64"]
    assert [option.label for option in view.children[0].options] == [
        "エメラルド x32 → ダイヤモンド x1",
        "エメラルド x64 → ダイヤモンド x2",
    ]


def test_emerald_confirmation_preserves_request_and_emerald_count() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.confirm_emerald_diamond_exchange = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(status="completed", emerald_count=32, diamond_count=1)
        )
        view = EmeraldDiamondConfirmView(
            bot,
            owner_id=123,
            request_id="00000000-0000-4000-8000-000000000032",
            emerald_count=32,
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock()),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.confirm.callback(interaction)  # type: ignore[arg-type]

        bot.confirm_emerald_diamond_exchange.assert_awaited_once_with(  # type: ignore[attr-defined]
            interaction,
            request_id="00000000-0000-4000-8000-000000000032",
            emerald_count=32,
        )
        interaction.followup.send.assert_awaited_once_with(
            "交換しました: エメラルド x32 → ダイヤモンド x1", ephemeral=True
        )

    asyncio.run(exercise())


def test_open_resource_shop_refreshes_public_panel_and_private_menu_from_same_rates(
    tmp_path,
) -> None:
    bot, _ = _bot(tmp_path)
    packs = (
        MinecraftResourcePack("minecraft:emerald", "エメラルド", 4, 100),
        MinecraftResourcePack("minecraft:emerald", "エメラルド", 16, 360),
    )
    shop = MinecraftResourceShop(MinecraftXpWallet(600, 100, 500), packs)
    bot._level_bot_xp.fetch_resource_shop = AsyncMock(  # type: ignore[method-assign]
        return_value=shop
    )
    interaction = SimpleNamespace(
        guild_id=456,
        user=SimpleNamespace(id=123),
        message=SimpleNamespace(edit=AsyncMock()),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    asyncio.run(bot.show_minecraft_resource_shop(interaction))  # type: ignore[arg-type]

    bot._level_bot_xp.fetch_resource_shop.assert_awaited_once_with(456, 123)  # type: ignore[attr-defined]
    interaction.message.edit.assert_awaited_once()
    public_embed = interaction.message.edit.await_args.kwargs["embed"]
    assert "`サーバーXP 100` → `エメラルド x4`" in str(public_embed.fields[0].value)
    assert "`サーバーXP 360` → `エメラルド x16`" in str(public_embed.fields[0].value)
    interaction.followup.send.assert_awaited_once()
    private_view = interaction.followup.send.await_args.kwargs["view"]
    assert [option.label for option in private_view.children[0].options] == [
        "エメラルド x4 (サーバーXP 100)",
        "エメラルド x16 (サーバーXP 360)",
    ]


def test_resource_commands_allow_only_fixed_items_and_recipient() -> None:
    assert resource_give_command("Steve", "minecraft:diamond", 3) == (
        "give Steve minecraft:diamond 3"
    )
    assert resource_give_command("*Steve", "minecraft:diamond", 1) == (
        "give *Steve minecraft:diamond 1"
    )
    actionbar = resource_exchange_actionbar_command("Steve", "minecraft:emerald", 4, 100)
    assert actionbar.startswith("title Steve actionbar ")
    assert "エメラルド x4" in actionbar
    tellraw = resource_exchange_tellraw_command(
        "うさぽサーバー", "Steve", "minecraft:emerald", 4, 100
    )
    assert tellraw.startswith("tellraw @a ")
    assert "エメラルド x4" in tellraw
    with pytest.raises(ValueError):
        resource_give_command("@a", "minecraft:diamond", 1)
    with pytest.raises(ValueError):
        resource_give_command("Steve", "minecraft:netherite_ingot", 1)
    with pytest.raises(ValueError):
        resource_give_command("Steve", "minecraft:diamond", 65)
    assert resource_give_command("Steve", "minecraft:emerald", 1).endswith(" 1")
    assert resource_give_command("Steve", "minecraft:emerald", 64).endswith(" 64")


def test_confirmation_preserves_item_count_and_cost_mapping() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.confirm_minecraft_resource_exchange = AsyncMock(  # type: ignore[method-assign]
            return_value=None
        )
        pack = MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 3, 2_160)
        view = MinecraftResourceConfirmView(
            bot,
            owner_id=123,
            request_id="00000000-0000-4000-8000-000000000020",
            pack=pack,
            affordable=True,
        )
        interaction = AsyncMock()

        await view.confirm.callback(interaction)

        bot.confirm_minecraft_resource_exchange.assert_awaited_once_with(
            interaction,
            request_id="00000000-0000-4000-8000-000000000020",
            item_id="minecraft:diamond",
            item_count=3,
            expected_cost_xp=2_160,
        )

    asyncio.run(exercise())


def test_confirmation_preserves_one_stack_item_count_and_cost_mapping() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.confirm_minecraft_resource_exchange = AsyncMock(  # type: ignore[method-assign]
            return_value=None
        )
        pack = MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 64, 46_080)
        view = MinecraftResourceConfirmView(
            bot,
            owner_id=123,
            request_id="00000000-0000-4000-8000-000000000064",
            pack=pack,
            affordable=True,
        )
        interaction = AsyncMock()

        await view.confirm.callback(interaction)

        bot.confirm_minecraft_resource_exchange.assert_awaited_once_with(
            interaction,
            request_id="00000000-0000-4000-8000-000000000064",
            item_id="minecraft:diamond",
            item_count=64,
            expected_cost_xp=46_080,
        )

    asyncio.run(exercise())


def test_parses_resource_shop_and_delivery_event() -> None:
    shop = LevelBotXpClient._parse_resource_shop(
        {
            "wallet": {"total_xp": 600, "spent_xp": 100, "available_xp": 500},
            "packs": [
                {
                    "item_id": "minecraft:emerald",
                    "item_name": "エメラルド",
                    "item_count": 4,
                    "cost_xp": 100,
                }
            ],
        }
    )
    event = LevelBotXpClient._parse_resource_exchange(
        {
            "id": 7,
            "event_id": "resource-exchange-7",
            "guild_id": "456",
            "user_id": "123",
            "minecraft_account_id": "mc-bot:1",
            "item_id": "minecraft:diamond",
            "item_name": "ダイヤモンド",
            "item_count": 3,
            "cost_xp": 550,
            "status": "pending",
        }
    )

    assert shop.packs[0].item_count == 4
    assert event.item_id == "minecraft:diamond"
    with pytest.raises(ValueError):
        LevelBotXpClient._parse_resource_pack(
            {
                "item_id": "minecraft:netherite_ingot",
                "item_name": "ネザライト",
                "item_count": 1,
                "cost_xp": 1,
            }
        )


def test_sync_grants_resource_once_and_announces_like_xp_exchange(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    rcon = ResourceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    event = _event()
    event = replace(event, minecraft_account_id=f"mc-bot:{account.id}")
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    update = AsyncMock(return_value=True)
    bot._level_bot_xp.update_resource_exchange = update  # type: ignore[method-assign]
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]

    asyncio.run(
        bot._sync_minecraft_resource_exchanges(
            guild_id=456,
            online_names={"steve"},
            linked_accounts=(account,),
        )
    )

    assert rcon.commands.count("give Steve minecraft:diamond 3") == 1
    assert sum(command.startswith("title Steve actionbar ") for command in rcon.commands) == 1
    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 1
    assert [call.args[2] for call in update.await_args_list] == ["claim", "complete"]
    delivery = bot._accounts.get_minecraft_resource_exchange_delivery(event.event_id)
    assert delivery is not None
    assert delivery.reward_applied
    assert delivery.level_completed
    assert delivery.minecraft_notified
    assert delivery.minecraft_public_notified
    assert delivery.discord_notified
    send_log.assert_awaited_once()
    assert "ダイヤモンド x3" in str(send_log.await_args.args[0].description)


def test_sync_refunds_explicit_give_failure(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    rcon = ResourceRcon(response="No player was found")
    bot._rcon = rcon  # type: ignore[assignment]
    event = _event()
    event = replace(event, minecraft_account_id=f"mc-bot:{account.id}")
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    update = AsyncMock(return_value=True)
    bot._level_bot_xp.update_resource_exchange = update  # type: ignore[method-assign]

    asyncio.run(
        bot._sync_minecraft_resource_exchanges(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )
    )

    assert [call.args[2] for call in update.await_args_list] == ["claim", "cancel"]
    assert bot._accounts.get_minecraft_resource_exchange_delivery(event.event_id) is None


def test_sync_never_retries_ambiguous_give(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    rcon = ResourceRcon()
    rcon.exception = OSError("RCON response lost")
    bot._rcon = rcon  # type: ignore[assignment]
    pending = _event()
    pending = replace(pending, minecraft_account_id=f"mc-bot:{account.id}")
    delivering = replace(pending, status="delivering")
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[pending], [delivering]]
    )
    update = AsyncMock(return_value=True)
    bot._level_bot_xp.update_resource_exchange = update  # type: ignore[method-assign]

    async def exercise() -> None:
        for _ in range(2):
            await bot._sync_minecraft_resource_exchanges(
                guild_id=456, online_names={"steve"}, linked_accounts=(account,)
            )

    asyncio.run(exercise())

    assert rcon.commands.count("give Steve minecraft:diamond 3") == 1
    delivery = bot._accounts.get_minecraft_resource_exchange_delivery(pending.event_id)
    assert delivery is not None
    assert not delivery.reward_applied
    assert all(call.args[2] == "claim" for call in update.await_args_list)


def test_sync_cancels_resource_exchange_when_player_is_offline(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    rcon = ResourceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    event = replace(_event(), minecraft_account_id=f"mc-bot:{account.id}")
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    update = AsyncMock(return_value=True)
    bot._level_bot_xp.update_resource_exchange = update  # type: ignore[method-assign]

    asyncio.run(
        bot._sync_minecraft_resource_exchanges(
            guild_id=456,
            online_names=set(),
            linked_accounts=(account,),
        )
    )

    update.assert_awaited_once_with(7, 456, "cancel", claim_token=None)
    assert not rcon.commands


def test_sync_resource_exchange_retries_lost_claim_with_same_token(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    rcon = ResourceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    pending = replace(_event(), minecraft_account_id=f"mc-bot:{account.id}")
    delivering = replace(pending, status="delivering")
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[pending], [delivering]]
    )
    update = AsyncMock(side_effect=[False, True, True])
    bot._level_bot_xp.update_resource_exchange = update  # type: ignore[method-assign]

    async def exercise() -> None:
        for _ in range(2):
            await bot._sync_minecraft_resource_exchanges(
                guild_id=456,
                online_names={"steve"},
                linked_accounts=(account,),
            )

    asyncio.run(exercise())

    first_token = update.await_args_list[0].kwargs["claim_token"]
    assert update.await_args_list[1].kwargs["claim_token"] == first_token
    assert rcon.commands.count("give Steve minecraft:diamond 3") == 1
    delivery = bot._accounts.get_minecraft_resource_exchange_delivery(pending.event_id)
    assert delivery is not None
    assert delivery.level_completed


def test_sync_resource_exchange_retries_completion_and_each_notification(
    tmp_path,
) -> None:
    bot, account = _bot(tmp_path)
    rcon = ResourceRcon()
    rcon.actionbar_failures = 1
    rcon.tellraw_failures = 1
    bot._rcon = rcon  # type: ignore[assignment]
    event = replace(_event(), minecraft_account_id=f"mc-bot:{account.id}")
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[event], []]
    )
    bot._level_bot_xp.update_resource_exchange = AsyncMock(  # type: ignore[method-assign]
        side_effect=[True, False, True]
    )
    send_log = AsyncMock(side_effect=[RuntimeError("Discord unavailable"), None])
    bot._send = send_log  # type: ignore[method-assign]

    async def exercise() -> None:
        for _ in range(2):
            await bot._sync_minecraft_resource_exchanges(
                guild_id=456,
                online_names={"steve"},
                linked_accounts=(account,),
            )

    asyncio.run(exercise())

    assert rcon.commands.count("give Steve minecraft:diamond 3") == 1
    assert sum(command.startswith("title Steve actionbar ") for command in rcon.commands) == 2
    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 2
    assert send_log.await_count == 2
    delivery = bot._accounts.get_minecraft_resource_exchange_delivery(event.event_id)
    assert delivery is not None
    assert delivery.level_completed
    assert delivery.minecraft_notified
    assert delivery.minecraft_public_notified
    assert delivery.discord_notified
