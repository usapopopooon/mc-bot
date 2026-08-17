import asyncio
from unittest.mock import AsyncMock, Mock

import discord

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import (
    MinecraftXpExchangeRequest,
    MinecraftXpPack,
    MinecraftXpShop,
    MinecraftXpWallet,
)
from mc_bot.xp_shop import (
    MinecraftXpConfirmView,
    MinecraftXpPackSelectView,
    MinecraftXpShopPanelView,
    minecraft_xp_shop_embed,
    wallet_text,
)


def test_minecraft_xp_shop_panel_lists_api_packs() -> None:
    embed = minecraft_xp_shop_embed(
        (
            MinecraftXpPack(10, 50),
            MinecraftXpPack(50, 250),
            MinecraftXpPack(100, 500),
            MinecraftXpPack(1_000, 5_000),
        )
    )

    assert embed.title == "Minecraft XP交換所"
    assert embed.fields[0].value == (
        "`サーバーXP 10` → `Minecraft 50 XP`\n"
        "`サーバーXP 50` → `Minecraft 250 XP`\n"
        "`サーバーXP 100` → `Minecraft 500 XP`\n"
        "`サーバーXP 1,000` → `Minecraft 5,000 XP (Lv.0からLv.50近く)`"
    )
    assert embed.fields[1].name == "🎮 ゲーム内コマンド"
    assert "`/exchange`" in str(embed.fields[1].value)
    assert "`/exchange xp <50|250|500|5000>`" in str(embed.fields[1].value)
    assert "`/exchange balance`" in str(embed.fields[1].value)
    assert "受け取りたいMinecraft XP量" in str(embed.fields[1].value)
    assert embed.fields[2].name == "⚠️ 交換前にご確認ください"
    assert "参加していない状態ではMinecraft XPは加算されません" in str(embed.fields[2].value)
    assert "サーバーXPも消費されません" in str(embed.fields[2].value)
    assert embed.fields[3].name == "📢 交換完了時の通知"
    assert "Discordのログチャンネル" in str(embed.fields[3].value)
    assert "Minecraft内チャット" in str(embed.fields[3].value)
    assert embed.footer.text == "残高・選択・確認画面は本人にのみ表示されます"


def test_minecraft_xp_shop_panel_has_persistent_buttons() -> None:
    async def build_view() -> MinecraftXpShopPanelView:
        return MinecraftXpShopPanelView(MinecraftDiscordBot(Config(discord_token="secret")))

    view = asyncio.run(build_view())

    assert view.timeout is None
    assert [child.label for child in view.children] == ["XPを交換", "自分のXP"]
    assert [child.custom_id for child in view.children] == [
        "mc-xp-shop:open",
        "mc-xp-shop:balance",
    ]


def test_shop_selection_and_confirmation_use_owner_wallet() -> None:
    async def build_views() -> tuple[MinecraftXpPackSelectView, MinecraftXpConfirmView]:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        shop = MinecraftXpShop(
            wallet=MinecraftXpWallet(total_xp=2_000, spent_xp=25, available_xp=1_975),
            packs=(
                MinecraftXpPack(10, 50),
                MinecraftXpPack(100, 500),
                MinecraftXpPack(1_000, 5_000),
            ),
        )
        return (
            MinecraftXpPackSelectView(bot, owner_id=123, shop=shop),
            MinecraftXpConfirmView(
                bot,
                owner_id=123,
                request_id="00000000-0000-4000-8000-000000000001",
                cost_xp=100,
                expected_reward_xp=500,
                affordable=False,
            ),
        )

    select_view, confirm_view = asyncio.run(build_views())

    select = select_view.children[0]
    assert [option.value for option in select.options] == ["10", "100", "1000"]
    assert select.options[-1].label == ("サーバーXP 1,000 → Minecraft 5,000 XP (Lv.0からLv.50近く)")
    assert confirm_view.confirm.disabled


def test_wallet_text_matches_color_shop_wording() -> None:
    assert wallet_text(MinecraftXpWallet(100, 25, 75)) == (
        "獲得XP: **100 XP**\n消費済み: **25 XP**\n現在XP: **75 XP**"
    )


def test_successful_confirmation_is_single_use_and_passes_expected_rate() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.confirm_minecraft_xp_exchange = AsyncMock(  # type: ignore[method-assign]
            return_value=MinecraftXpExchangeRequest(
                status="reserved",
                message="交換を受け付けました。",
                wallet_before=MinecraftXpWallet(100, 0, 100),
                wallet_after=MinecraftXpWallet(100, 10, 90),
                pack=MinecraftXpPack(10, 50),
            )
        )
        view = MinecraftXpConfirmView(
            bot,
            owner_id=123,
            request_id="00000000-0000-4000-8000-000000000002",
            cost_xp=10,
            expected_reward_xp=50,
            affordable=True,
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.response.edit_message = AsyncMock()
        interaction.followup.send = AsyncMock()

        await view.confirm.callback(interaction)

        bot.confirm_minecraft_xp_exchange.assert_awaited_once_with(
            interaction,
            request_id="00000000-0000-4000-8000-000000000002",
            cost_xp=10,
            expected_reward_xp=50,
        )
        assert all(child.disabled for child in view.children)
        interaction.followup.send.assert_awaited_once_with("交換を受け付けました。", ephemeral=True)

    asyncio.run(exercise())


def test_unknown_confirmation_result_reenables_same_id_for_safe_retry() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.confirm_minecraft_xp_exchange = AsyncMock(  # type: ignore[method-assign]
            return_value=None
        )
        view = MinecraftXpConfirmView(
            bot,
            owner_id=123,
            request_id="00000000-0000-4000-8000-000000000003",
            cost_xp=10,
            expected_reward_xp=50,
            affordable=True,
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup.send = AsyncMock()

        await view.confirm.callback(interaction)
        await view.confirm.callback(interaction)

        assert bot.confirm_minecraft_xp_exchange.await_count == 2
        for call in bot.confirm_minecraft_xp_exchange.await_args_list:
            assert call.kwargs["request_id"] == ("00000000-0000-4000-8000-000000000003")
        assert not view.confirm.disabled
        assert not view.cancel.disabled

    asyncio.run(exercise())
