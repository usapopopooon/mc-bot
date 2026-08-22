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
    MinecraftResourceCatalog,
    MinecraftResourceExchangeEvent,
    MinecraftResourcePack,
    MinecraftResourceShop,
    MinecraftXpWallet,
)
from mc_bot.resource_catalog import (
    resource_catalog_sync_command,
    resource_pack_validation_command,
)
from mc_bot.resource_shop import (
    DiamondEmeraldConfirmView,
    EmeraldDiamondConfirmView,
    EmeraldDiamondPackSelectView,
    MinecraftResourceConfirmView,
    MinecraftResourcePackSelectView,
    MinecraftResourceShopPanelView,
    minecraft_resource_shop_embed,
    resource_catalog_management_embed,
    resource_exchange_actionbar_command,
    resource_exchange_tellraw_command,
    resource_give_command,
)


class ResourceRcon:
    def __init__(
        self,
        *,
        response: str = "Gave 3 [Diamond] to Steve",
        catalog_response: str | None = None,
    ) -> None:
        self.response = response
        self.catalog_response = catalog_response
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
        if command.startswith("usapo-event-bridge resource-catalog-sync "):
            revision = command.split()[2]
            return self.catalog_response or (
                f"Resource catalog synchronized: revision {revision} (1 packs)"
            )
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


def test_resource_panel_lists_dynamic_server_rates_and_is_persistent() -> None:
    packs = (
        MinecraftResourcePack("minecraft:emerald", "エメラルド", 4, 100),
        MinecraftResourcePack("minecraft:emerald", "エメラルド", 16, 360),
        MinecraftResourcePack("minecraft:gunpowder", "火薬", 8, 100),
        MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 3, 550),
        MinecraftResourcePack("minecraft:copper_ingot", "銅インゴット", 16, 240),
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
    assert "手持ちのダイヤモンドとエメラルドも両替" in str(embed.description)
    assert "**サーバーXP → 資源**" in str(embed.fields[0].value)
    assert "**🟢 エメラルド**" in str(embed.fields[0].value)
    assert "**🧨 火薬**" in str(embed.fields[0].value)
    assert "**💎 ダイヤモンド**" in str(embed.fields[0].value)
    assert "**📦 銅インゴット**" in str(embed.fields[0].value)
    assert "`サーバーXP 100` → `エメラルド x4`" in str(embed.fields[0].value)
    assert "`サーバーXP 360` → `エメラルド x16`" in str(embed.fields[0].value)
    assert "`サーバーXP 100` → `火薬 x8`" in str(embed.fields[0].value)
    assert "**手持ち資源 → 資源**" in str(embed.fields[0].value)
    assert "`エメラルド x32` → `ダイヤモンド x1`" in str(embed.fields[0].value)
    assert "`エメラルド x64` → `ダイヤモンド x2`" in str(embed.fields[0].value)
    assert "`ダイヤモンド x1` → `エメラルド x16`" in str(embed.fields[0].value)
    assert "`ダイヤモンド x4` → `エメラルド x64`" in str(embed.fields[0].value)
    assert "**エメラルド**" in str(embed.fields[0].value)
    assert "`エメラルド x64` → `500 サーバーXP`" in str(embed.fields[0].value)
    assert "**資材**" in str(embed.fields[0].value)
    assert "`砂岩 x64` → `50 サーバーXP`" in str(embed.fields[0].value)
    assert "1人1日 **3,000 サーバーXP**" in str(embed.fields[0].value)
    assert "毎日0時・日本時間に更新" in str(embed.fields[0].value)
    assert "上限超過時は資源を回収しません" in str(embed.fields[0].value)
    assert "名前や特殊データのない通常アイテム" in str(embed.fields[0].value)
    assert embed.fields[1].name == "🎮 ゲーム内コマンド"
    assert "`/exchange`" in str(embed.fields[1].value)
    assert "`/exchange resource <資源ID> <個数>`" in str(embed.fields[1].value)
    assert "`/exchange emerald-diamond <32|64>`" in str(embed.fields[1].value)
    assert "`/exchange diamond-emerald <1|4>`" in str(embed.fields[1].value)
    assert "`/exchange buyback <1|2|4|8|16|max|all>`" in str(embed.fields[1].value)
    assert "`/exchange balance`" in str(embed.fields[1].value)
    assert "copper_ingotは `16`" in str(embed.fields[1].value)
    assert "足元へドロップ" in str(embed.fields[2].value)
    assert embed.fields[3].name == "📢 交換完了時の通知"
    assert "**Discordのログチャンネル**" in str(embed.fields[3].value)
    assert "**Minecraft内チャット**" in str(embed.fields[3].value)
    assert "当日の残り売却枠" in str(embed.fields[3].value)
    assert "Minecraft内で本人だけ" in str(embed.fields[3].value)
    assert embed.footer.text == "残高・選択・確認画面は本人にのみ表示されます"
    assert panel.timeout is None
    assert [child.custom_id for child in panel.children] == [
        "mc-resource-shop:open",
        "mc-resource-shop:emerald-diamond",
        "mc-resource-shop:balance",
    ]
    assert [option.value for option in select.children[0].options] == ["0", "1", "2", "3", "4"]
    assert [option.label for option in select.children[0].options] == [
        "エメラルド x4",
        "エメラルド x16",
        "火薬 x8",
        "ダイヤモンド x3",
        "銅インゴット x16",
    ]
    assert [option.description for option in select.children[0].options] == [
        "必要: 100 サーバーXP",
        "必要: 360 サーバーXP",
        "必要: 100 サーバーXP",
        "必要: 550 サーバーXP",
        "必要: 240 サーバーXP",
    ]


def test_resource_panel_stays_within_discord_limits_at_maximum_catalog_size() -> None:
    packs = tuple(
        MinecraftResourcePack(
            f"minecraft:item_{index}",
            f"表示名{index}" + "長" * 50,
            64,
            10_000_000,
        )
        for index in range(25)
    )

    embed = minecraft_resource_shop_embed(packs)

    assert len(embed) <= 6000
    assert len(embed.fields) <= 25
    assert all(len(str(field.value)) <= 1024 for field in embed.fields)

    management = resource_catalog_management_embed(
        packs,
        revision=7,
        synchronized=False,
        synchronization_error="Resource pack rejected: invalid item",
    )
    assert len(management) <= 6000
    assert all(len(str(field.value)) <= 1024 for field in management.fields)
    assert "未反映" in str(management.footer.text)


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
    assert [option.value for option in view.children[1].options] == ["1", "4"]
    assert [option.label for option in view.children[1].options] == [
        "ダイヤモンド x1 → エメラルド x16",
        "ダイヤモンド x4 → エメラルド x64",
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


def test_diamond_confirmation_preserves_request_and_diamond_count() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.confirm_diamond_emerald_exchange = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(status="completed", diamond_count=4, emerald_count=64)
        )
        view = DiamondEmeraldConfirmView(
            bot,
            owner_id=123,
            request_id="00000000-0000-4000-8000-000000000064",
            diamond_count=4,
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock()),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.confirm.callback(interaction)  # type: ignore[arg-type]

        bot.confirm_diamond_emerald_exchange.assert_awaited_once_with(  # type: ignore[attr-defined]
            interaction,
            request_id="00000000-0000-4000-8000-000000000064",
            diamond_count=4,
        )
        interaction.followup.send.assert_awaited_once_with(
            "交換しました: ダイヤモンド x4 → エメラルド x64", ephemeral=True
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
        "エメラルド x4",
        "エメラルド x16",
    ]
    assert [option.description for option in private_view.children[0].options] == [
        "必要: 100 サーバーXP",
        "必要: 360 サーバーXP",
    ]


def test_resource_commands_allow_dynamic_safe_items_and_only_the_recipient() -> None:
    assert resource_give_command("Steve", "minecraft:diamond", 3) == (
        "give Steve minecraft:diamond 3"
    )
    assert resource_give_command("*Steve", "minecraft:diamond", 1) == (
        "give *Steve minecraft:diamond 1"
    )
    actionbar = resource_exchange_actionbar_command(
        "Steve", "minecraft:emerald", "エメラルド", 4, 100
    )
    assert actionbar.startswith("title Steve actionbar ")
    assert "エメラルド x4" in actionbar
    tellraw = resource_exchange_tellraw_command(
        "うさぽサーバー", "Steve", "minecraft:emerald", "エメラルド", 4, 100
    )
    assert tellraw.startswith("tellraw @a ")
    assert "エメラルド x4" in tellraw
    with pytest.raises(ValueError):
        resource_give_command("@a", "minecraft:diamond", 1)
    assert resource_give_command("Steve", "minecraft:netherite_ingot", 1) == (
        "give Steve minecraft:netherite_ingot 1"
    )
    with pytest.raises(ValueError):
        resource_give_command("Steve", "minecraft:diamond @a", 1)
    with pytest.raises(ValueError):
        resource_give_command("Steve", "minecraft:diamond", 65)
    assert resource_give_command("Steve", "minecraft:emerald", 1).endswith(" 1")
    assert resource_give_command("Steve", "minecraft:emerald", 64).endswith(" 64")
    assert resource_give_command("Steve", "minecraft:gunpowder", 64) == (
        "give Steve minecraft:gunpowder 64"
    )


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
    dynamic = LevelBotXpClient._parse_resource_pack(
        {
            "item_id": "minecraft:netherite_ingot",
            "item_name": "ネザライトインゴット",
            "item_count": 1,
            "cost_xp": 1,
        }
    )
    assert dynamic.item_id == "minecraft:netherite_ingot"
    with pytest.raises(ValueError):
        LevelBotXpClient._parse_resource_pack(
            {
                "item_id": "minecraft:diamond @a",
                "item_name": "危険",
                "item_count": 1,
                "cost_xp": 1,
            }
        )
    with pytest.raises(ValueError):
        LevelBotXpClient._parse_resource_pack(
            {
                "item_id": "minecraft:diamond",
                "item_name": "ダイヤモンド",
                "item_count": 1,
                "cost_xp": 10_000_001,
            }
        )


def test_parses_and_encodes_dynamic_resource_catalog() -> None:
    catalog = LevelBotXpClient._parse_resource_catalog(
        {
            "guild_id": "456",
            "revision": 3,
            "packs": [
                {
                    "item_id": "minecraft:copper_ingot",
                    "item_name": "銅インゴット",
                    "item_count": 16,
                    "cost_xp": 240,
                }
            ],
        }
    )

    assert catalog == MinecraftResourceCatalog(
        guild_id=456,
        revision=3,
        packs=(MinecraftResourcePack("minecraft:copper_ingot", "銅インゴット", 16, 240),),
    )
    command = resource_catalog_sync_command(catalog.revision, catalog.packs)
    assert command.startswith("usapo-event-bridge resource-catalog-sync 3 ")
    assert resource_pack_validation_command(catalog.packs[0]).startswith(
        "usapo-event-bridge resource-pack-validate "
    )

    with pytest.raises(ValueError):
        LevelBotXpClient._parse_resource_catalog(
            {
                "guild_id": "456",
                "revision": 4,
                "packs": [
                    {
                        "item_id": "minecraft:copper_ingot",
                        "item_name": "銅インゴット",
                        "item_count": 4,
                        "cost_xp": 75,
                    },
                    {
                        "item_id": "minecraft:copper_ingot",
                        "item_name": "別名",
                        "item_count": 16,
                        "cost_xp": 240,
                    },
                ],
            }
        )


def test_catalog_sync_applies_once_and_periodically_reverifies_after_restart_window(
    tmp_path,
) -> None:
    bot, _ = _bot(tmp_path)
    bot._settings = replace(bot._settings, guild_id=456)
    rcon = ResourceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    catalog = MinecraftResourceCatalog(
        guild_id=456,
        revision=3,
        packs=(MinecraftResourcePack("minecraft:copper_ingot", "銅インゴット", 16, 240),),
    )
    bot._level_bot_xp.fetch_resource_catalog = AsyncMock(  # type: ignore[method-assign]
        return_value=catalog
    )

    assert asyncio.run(bot._sync_resource_catalog())
    assert asyncio.run(bot._sync_resource_catalog())
    commands = [
        command
        for command in rcon.commands
        if command.startswith("usapo-event-bridge resource-catalog-sync ")
    ]
    assert len(commands) == 1

    bot._resource_catalog_verified_at -= 301
    assert asyncio.run(bot._sync_resource_catalog())
    commands = [
        command
        for command in rcon.commands
        if command.startswith("usapo-event-bridge resource-catalog-sync ")
    ]
    assert len(commands) == 2


def test_catalog_sync_does_not_accept_a_rejection_that_mentions_the_revision(tmp_path) -> None:
    bot, _ = _bot(tmp_path)
    bot._settings = replace(bot._settings, guild_id=456)
    bot._rcon = ResourceRcon(  # type: ignore[assignment]
        catalog_response="Resource catalog rejected: conflict with revision 3"
    )
    catalog = MinecraftResourceCatalog(
        guild_id=456,
        revision=3,
        packs=(MinecraftResourcePack("minecraft:copper_ingot", "銅インゴット", 16, 240),),
    )
    bot._level_bot_xp.fetch_resource_catalog = AsyncMock(  # type: ignore[method-assign]
        return_value=catalog
    )

    assert not asyncio.run(bot._sync_resource_catalog())
    assert bot._resource_catalog_revision is None
    assert bot._resource_catalog_last_error == (
        "Resource catalog rejected: conflict with revision 3"
    )


def test_catalog_sync_rejects_a_catalog_for_another_discord_guild(tmp_path) -> None:
    bot, _ = _bot(tmp_path)
    bot._settings = replace(bot._settings, guild_id=456)
    rcon = ResourceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    catalog = MinecraftResourceCatalog(
        guild_id=999,
        revision=3,
        packs=(MinecraftResourcePack("minecraft:copper_ingot", "銅インゴット", 16, 240),),
    )

    assert not asyncio.run(bot._sync_resource_catalog(catalog=catalog))
    assert not any("resource-catalog-sync" in command for command in rcon.commands)
    assert bot._resource_catalog_last_error == "別のDiscordサーバーのカタログです"


def test_parses_gunpowder_pack_and_delivery_event() -> None:
    pack = LevelBotXpClient._parse_resource_pack(
        {
            "item_id": "minecraft:gunpowder",
            "item_name": "火薬",
            "item_count": 64,
            "cost_xp": 150,
        }
    )
    event = LevelBotXpClient._parse_resource_exchange(
        {
            "id": 8,
            "event_id": "resource-exchange-8",
            "guild_id": "456",
            "user_id": "123",
            "minecraft_account_id": "mc-bot:1",
            "item_id": "minecraft:gunpowder",
            "item_name": "火薬",
            "item_count": 64,
            "cost_xp": 150,
            "status": "pending",
        }
    )

    assert (pack.item_name, pack.item_count, pack.cost_xp) == ("火薬", 64, 150)
    assert (event.item_id, event.item_name, event.item_count, event.cost_xp) == (
        "minecraft:gunpowder",
        "火薬",
        64,
        150,
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


def test_sync_delivers_gunpowder_pack_with_exact_api_values(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    rcon = ResourceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    event = replace(
        _event(),
        minecraft_account_id=f"mc-bot:{account.id}",
        item_id="minecraft:gunpowder",
        item_name="火薬",
        item_count=64,
        cost_xp=150,
    )
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    bot._level_bot_xp.update_resource_exchange = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    bot._send = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(
        bot._sync_minecraft_resource_exchanges(
            guild_id=456,
            online_names={"steve"},
            linked_accounts=(account,),
        )
    )

    assert rcon.commands.count("give Steve minecraft:gunpowder 64") == 1
    delivery = bot._accounts.get_minecraft_resource_exchange_delivery(event.event_id)
    assert delivery is not None
    assert (delivery.item_id, delivery.item_name, delivery.item_count, delivery.cost_xp) == (
        "minecraft:gunpowder",
        "火薬",
        64,
        150,
    )


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
