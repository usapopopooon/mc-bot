import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.market import MarketListing
from mc_bot.market_ui import MarketListingView, MarketPanelView
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
    view = MarketPanelView(bot)

    custom_ids = {item.custom_id for item in view.children}
    labels = {item.label for item in view.children}

    assert custom_ids == {"mc-market:guide:0", "mc-market:balance:0"}
    assert labels == {"使い方", "サーバーXP確認"}


def test_market_listing_view_only_has_transaction_buttons() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    view = MarketListingView(bot, 17)

    custom_ids = {item.custom_id for item in view.children}
    labels = {item.label for item in view.children}

    assert custom_ids == {"mc-market:buy:17", "mc-market:cancel:17"}
    assert labels == {"購入", "出品取消"}


def test_market_recovery_refreshes_existing_active_listing() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(guild_id=1)
    bot._market.list_open = Mock(  # type: ignore[method-assign]
        return_value=[SimpleNamespace(status="active", discord_message_id=901, listing_id=17)]
    )
    bot._refresh_market_listing = AsyncMock()  # type: ignore[method-assign]
    bot._deliver_market_purchase_notifications = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(bot._recover_market_transactions())

    bot._refresh_market_listing.assert_awaited_once_with(  # type: ignore[attr-defined]
        17, move_panel=False
    )
    bot._deliver_market_purchase_notifications.assert_awaited_once()  # type: ignore[attr-defined]


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


def test_refresh_market_panel_reuses_existing_message_on_restart(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(discord_token="secret", settings_path=tmp_path / "settings.json")
    )
    existing_message = FakeMarketPanelMessage(800)
    channel = FakeMarketPanelChannel(existing_message)
    bot._settings = RuntimeSettings(
        guild_id=1,
        market_channel_id=2,
        market_panel_message_id=existing_message.id,
    )
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )

    asyncio.run(bot._refresh_market_panel())

    assert not existing_message.deleted
    assert len(existing_message.edits) == 1
    assert channel.sent == []
    assert bot._settings.market_panel_message_id == 800


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


def test_sold_market_listing_is_deleted_instead_of_left_as_sold() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(guild_id=1, market_channel_id=2)
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
        status="sold",
        purchase_request_id="33333333-3333-4333-8333-333333333333",
        buyer_account_id=3,
        buyer_discord_user_id=2003,
        discord_message_id=901,
        created_at="2026-08-18T00:00:00+00:00",
        updated_at="2026-08-18T00:00:00+00:00",
    )
    message = FakeMarketPanelMessage(901)
    channel = FakeMarketPanelChannel(message)
    bot._market.get = Mock(return_value=listing)  # type: ignore[method-assign]
    bot._market.set_discord_message = Mock()  # type: ignore[method-assign]
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )

    asyncio.run(bot._refresh_market_listing(17))

    assert message.deleted
    assert message.edits == []
    assert channel.sent == []
    bot._market.set_discord_message.assert_called_once_with(17, None)  # type: ignore[attr-defined]


def test_market_recovery_removes_previously_sold_listing_cards() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(guild_id=1, market_channel_id=2)
    sold = SimpleNamespace(status="sold", discord_message_id=901, listing_id=17)
    bot._market.list_sold_with_discord_message = Mock(  # type: ignore[attr-defined]
        return_value=[sold]
    )
    bot._market.list_open = Mock(return_value=[])  # type: ignore[method-assign]
    bot._refresh_market_listing = AsyncMock()  # type: ignore[method-assign]
    bot._deliver_market_purchase_notifications = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(bot._recover_market_transactions())

    bot._refresh_market_listing.assert_awaited_once_with(  # type: ignore[attr-defined]
        17, move_panel=False
    )
    bot._deliver_market_purchase_notifications.assert_awaited_once()  # type: ignore[attr-defined]
