import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.market import MarketListing
from mc_bot.market_ui import MarketPanelView
from mc_bot.settings import RuntimeSettings


class FakeMarketPanelMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.deleted = False
        self.edits: list[dict[str, object]] = []

    async def delete(self) -> None:
        self.deleted = True

    async def edit(self, **options: object) -> None:
        self.edits.append(options)


class FakeMarketPanelChannel:
    def __init__(self, message: FakeMarketPanelMessage | None = None) -> None:
        self.message = message
        self.sent: list[dict[str, object]] = []
        self.next_message_id = 900

    async def fetch_message(self, message_id: int) -> FakeMarketPanelMessage:
        assert self.message is not None
        assert message_id == self.message.id
        return self.message

    async def send(self, **options: object) -> FakeMarketPanelMessage:
        self.sent.append(options)
        self.next_message_id += 1
        self.message = FakeMarketPanelMessage(self.next_message_id)
        return self.message


def test_market_panel_view_has_guide_and_balance_buttons() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    custom_ids = {item.custom_id for item in MarketPanelView(bot).children}

    assert custom_ids == {"mc-market:guide:0", "mc-market:balance:0"}


def test_refresh_market_panel_creates_and_persists_message(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(discord_token="secret", settings_path=tmp_path / "settings.json")
    )
    bot._settings = RuntimeSettings(guild_id=1, market_channel_id=2)
    channel = FakeMarketPanelChannel()
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )

    asyncio.run(bot._refresh_market_panel())

    assert bot._settings.market_panel_message_id == 901
    assert len(channel.sent) == 1
    assert isinstance(channel.sent[0]["view"], MarketPanelView)


def test_refresh_market_panel_reposts_it_after_new_content(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(discord_token="secret", settings_path=tmp_path / "settings.json")
    )
    old_message = FakeMarketPanelMessage(800)
    channel = FakeMarketPanelChannel(old_message)
    bot._settings = RuntimeSettings(
        guild_id=1,
        market_channel_id=2,
        market_panel_message_id=old_message.id,
    )
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )

    asyncio.run(bot._refresh_market_panel(move_to_bottom=True))

    assert old_message.deleted
    assert bot._settings.market_panel_message_id == 901
    assert len(channel.sent) == 1


def test_market_channel_message_moves_panel_to_bottom() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(guild_id=1, market_channel_id=2)
    bot._refresh_market_panel = AsyncMock()  # type: ignore[method-assign]
    message = SimpleNamespace(
        channel=SimpleNamespace(id=2),
        author=SimpleNamespace(id=123),
    )

    asyncio.run(bot.on_message(message))  # type: ignore[arg-type]

    bot._refresh_market_panel.assert_awaited_once_with(  # type: ignore[attr-defined]
        move_to_bottom=True
    )


def test_new_listing_is_followed_by_market_panel() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(guild_id=1, market_channel_id=2)
    bot._market.set_discord_message = Mock()  # type: ignore[method-assign]
    bot._refresh_market_panel = AsyncMock()  # type: ignore[method-assign]
    channel = FakeMarketPanelChannel()
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )
    listing = MarketListing(
        listing_id=17,
        event_id="11111111-1111-4111-8111-111111111111",
        seller_account_id=2,
        seller_discord_user_id=2002,
        seller_uuid="22222222-2222-4222-8222-222222222222",
        seller_name="Seller",
        item_id="minecraft:diamond",
        item_name="ダイヤモンド",
        item_count=3,
        price_xp=720,
        status="active",
        purchase_request_id=None,
        buyer_account_id=None,
        buyer_discord_user_id=None,
        discord_message_id=None,
        created_at="2026-08-18T00:00:00+00:00",
        updated_at="2026-08-18T00:00:00+00:00",
    )

    asyncio.run(bot._post_market_listing(listing))

    bot._market.set_discord_message.assert_called_once_with(17, 901)  # type: ignore[attr-defined]
    bot._refresh_market_panel.assert_awaited_once_with(  # type: ignore[attr-defined]
        move_to_bottom=True
    )
