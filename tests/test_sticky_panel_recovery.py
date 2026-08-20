import asyncio
from unittest.mock import AsyncMock, Mock

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config


def test_ready_reuses_sticky_panels_without_moving_them() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._health_task = Mock()
    bot._health_task.done.return_value = False

    for name in (
        "_sync_whitelist_accounts",
        "_refresh_access_panel",
        "_refresh_admin_panel",
        "_refresh_xp_shop_panel",
        "_refresh_resource_shop_panel",
        "_refresh_item_gacha_panel",
        "_refresh_market_panel",
        "_refresh_quest_panel",
        "_refresh_online_player_cache",
        "_recover_market_transactions",
        "_recover_quests",
    ):
        setattr(bot, name, AsyncMock())

    bot._schedule_status_panel_refresh = Mock()  # type: ignore[method-assign]
    bot._ensure_market_recovery_started = Mock()  # type: ignore[method-assign]
    bot._ensure_minecraft_xp_started = Mock()  # type: ignore[method-assign]
    bot._ensure_activity_delivery_started = Mock()  # type: ignore[method-assign]

    asyncio.run(bot.on_ready())

    bot._refresh_market_panel.assert_awaited_once_with()  # type: ignore[attr-defined]
    bot._refresh_quest_panel.assert_awaited_once_with()  # type: ignore[attr-defined]
