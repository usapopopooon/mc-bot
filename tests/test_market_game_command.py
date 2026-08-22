import asyncio
import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import MinecraftMarketPurchaseRequest, MinecraftXpWallet
from mc_bot.settings import RuntimeSettings
from mc_bot.tailer import Cursor, PendingLine

SELLER_UUID = "22222222-2222-4222-8222-222222222222"
BUYER_UUID = "33333333-3333-4333-8333-333333333333"
LISTING_EVENT_ID = "11111111-1111-4111-8111-111111111111"
PURCHASE_ID = "44444444-4444-4444-8444-444444444444"
BALANCE_ID = "55555555-5555-4555-8555-555555555555"
CANCEL_ID = "66666666-6666-4666-8666-666666666666"


class LineTailer:
    def __init__(self, lines: list[PendingLine]) -> None:
        self.pending = lines
        self.acknowledged: list[PendingLine] = []

    async def lines(self):  # type: ignore[no-untyped-def]
        for line in self.pending:
            yield line

    def acknowledge(self, line: PendingLine) -> None:
        self.acknowledged.append(line)


class MarketRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("usapo-event-bridge market-deliver "):
            fields = command.split()
            return f"USAPO_MARKET_TRANSFER_RESULT|1|{fields[4]}|{fields[2]}|completed|sold|new"
        if command.startswith("usapo-event-bridge market-return "):
            fields = command.split()
            return f"USAPO_MARKET_TRANSFER_RESULT|1|{fields[4]}|{fields[2]}|completed|cancelled|new"
        if command.startswith("usapo-event-bridge market-mailbox-return "):
            fields = command.split()
            return f"USAPO_MARKET_TRANSFER_RESULT|1|{fields[4]}|{fields[2]}|completed|cancelled|new"
        if command.startswith("tellraw @a "):
            return ""
        if command.startswith("tellraw Buyer "):
            return ""
        raise AssertionError(f"unexpected RCON command: {command}")


class AmbiguousMarketRcon(MarketRcon):
    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("usapo-event-bridge market-deliver "):
            fields = command.split()
            return (
                f"USAPO_MARKET_TRANSFER_RESULT|1|{fields[4]}|{fields[2]}|"
                "storage_error|delivering|duplicate"
            )
        raise AssertionError(f"unexpected RCON command: {command}")


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _line(offset: int, message: str) -> PendingLine:
    return PendingLine(
        text=f"[08:24:00] [Server thread/INFO]: [UsapoEventBridge] {message}",
        cursor=Cursor("log-1", offset),
    )


def test_game_market_wires_listing_buyer_seller_price_and_delivery(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
            level_bot_api_url="http://level-bot",
            level_bot_api_token="secret",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    seller = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Seller",
        server_player_name="Seller",
        discord_user_id=2002,
        discord_username="seller",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=SELLER_UUID,
    )
    buyer = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Buyer",
        server_player_name="Buyer",
        discord_user_id=2003,
        discord_username="buyer",
        source="self",
        status="active",
        created_by=2003,
        player_uuid=BUYER_UUID,
    )
    bot._settings = RuntimeSettings(guild_id=1001, market_log_channel_id=777)
    bot._rcon = MarketRcon()  # type: ignore[assignment]
    wallet_before = MinecraftXpWallet(5_000, 0, 5_000)
    wallet_after = MinecraftXpWallet(5_000, 3_000, 2_000)
    bot._level_bot_xp.request_market_purchase = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftMarketPurchaseRequest(
            status="reserved",
            message="購入を予約しました。",
            request_id=PURCHASE_ID,
            wallet_before=wallet_before,
            wallet_after=wallet_after,
        )
    )
    bot._level_bot_xp.update_market_purchase = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    bot._level_bot_xp.fetch_market_wallet = AsyncMock(  # type: ignore[method-assign]
        return_value=wallet_after
    )
    market_log_channel = AsyncMock()
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=market_log_channel
    )
    bot._send = AsyncMock()  # type: ignore[method-assign]
    milliseconds = int(datetime.now(UTC).timestamp() * 1_000)
    lines = [
        _line(
            100,
            f"USAPO_MARKET_LISTING|1|{LISTING_EVENT_ID}|17|{SELLER_UUID}|"
            f"{_encode('Seller')}|{_encode('minecraft:ancient_debris')}|"
            f"{_encode('古代の残骸')}|2|3000|{milliseconds}",
        ),
        _line(
            200,
            f"USAPO_MARKET_REQUEST|1|{PURCHASE_ID}|buy|17|{BUYER_UUID}|"
            f"{_encode('Buyer')}|3000|{milliseconds}",
        ),
        _line(
            300,
            f"USAPO_MARKET_REQUEST|1|{BALANCE_ID}|balance|0|{BUYER_UUID}|"
            f"{_encode('Buyer')}|0|{milliseconds}",
        ),
    ]
    tailer = LineTailer(lines)
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == lines
    listing = bot._market.get(17)
    assert listing is not None and listing.status == "sold"
    bot._level_bot_xp.request_market_purchase.assert_awaited_once_with(  # type: ignore[attr-defined]
        request_id=PURCHASE_ID,
        guild_id=1001,
        listing_id=17,
        buyer_user_id=2003,
        seller_user_id=2002,
        buyer_account_id=buyer.id,
        seller_account_id=seller.id,
        expected_cost_xp=3_000,
    )
    bot._level_bot_xp.update_market_purchase.assert_awaited_once_with(  # type: ignore[attr-defined]
        request_id=PURCHASE_ID,
        guild_id=1001,
        action="complete",
    )
    bot._level_bot_xp.fetch_market_wallet.assert_awaited_once_with(  # type: ignore[attr-defined]
        1001,
        2003,
    )
    rcon = bot._rcon
    assert isinstance(rcon, MarketRcon)
    assert f"usapo-event-bridge market-deliver 17 {BUYER_UUID} {PURCHASE_ID}" in rcon.commands
    public_messages = [command for command in rcon.commands if command.startswith("tellraw @a ")]
    assert len(public_messages) == 1
    assert all(value in public_messages[0] for value in ("Buyer", "Seller", "古代の残骸", "3,000"))
    market_log_channel.send.assert_awaited_once()
    bot._send.assert_not_awaited()  # type: ignore[attr-defined]
    discord_log = str(market_log_channel.send.await_args.kwargs["embed"].description)
    assert all(value in discord_log for value in ("Buyer", "Seller", "古代の残骸", "3,000"))
    assert listing.minecraft_purchase_notified
    assert listing.discord_purchase_notified
    assert any("残りのサーバーXPは 2,000 XP" in command for command in rcon.commands)
    assert any("現在のサーバーXP: 2,000 XP" in command for command in rcon.commands)


def test_unlinked_listing_is_cancelled_to_persistent_mailbox(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    rcon = MarketRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._send_minecraft_private_message = AsyncMock()  # type: ignore[method-assign]
    milliseconds = int(datetime.now(UTC).timestamp() * 1_000)
    line = _line(
        100,
        f"USAPO_MARKET_LISTING|1|{LISTING_EVENT_ID}|17|{SELLER_UUID}|"
        f"{_encode('Seller')}|{_encode('minecraft:diamond')}|"
        f"{_encode('ダイヤモンド')}|3|500|{milliseconds}",
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    assert bot._market.get(17) is None
    assert (
        f"usapo-event-bridge market-mailbox-return 17 {SELLER_UUID} {LISTING_EVENT_ID}"
        in rcon.commands
    )
    bot._send_minecraft_private_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        "Seller",
        "マーケット利用にはDiscordアカウント連携が必要です。"
        "出品アイテムはフリマ返却受取箱へ移しました。",
    )


def test_terminal_listing_log_replay_does_not_start_a_second_return(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    seller = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Seller",
        server_player_name="Seller",
        discord_user_id=2002,
        discord_username="seller",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=SELLER_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id=LISTING_EVENT_ID,
        seller_account_id=seller.id,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="Seller",
        item_id="minecraft:diamond",
        item_name="ダイヤモンド",
        item_count=3,
        price_xp=500,
        created_at="2026-08-23T00:00:00+00:00",
    )
    assert bot._market.begin_cancel(
        listing_id=17,
        seller_account_id=seller.id,
        request_id=CANCEL_ID,
    )
    assert bot._market.set_status(17, CANCEL_ID, "cancelled")
    bot._accounts.update_status(seller.id, "missing")
    rcon = MarketRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    milliseconds = int(datetime.now(UTC).timestamp() * 1_000)
    line = _line(
        100,
        f"USAPO_MARKET_LISTING|1|{LISTING_EVENT_ID}|17|{SELLER_UUID}|"
        f"{_encode('Seller')}|{_encode('minecraft:diamond')}|"
        f"{_encode('ダイヤモンド')}|3|500|{milliseconds}",
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    assert rcon.commands == []


def test_game_market_keeps_xp_reserved_when_delivery_was_recorded_but_save_failed(
    tmp_path,
) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
            level_bot_api_url="http://level-bot",
            level_bot_api_token="secret",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    seller = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Seller",
        server_player_name="Seller",
        discord_user_id=2002,
        discord_username="seller",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=SELLER_UUID,
    )
    buyer = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Buyer",
        server_player_name="Buyer",
        discord_user_id=2003,
        discord_username="buyer",
        source="self",
        status="active",
        created_by=2003,
        player_uuid=BUYER_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id=LISTING_EVENT_ID,
        seller_account_id=seller.id,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="Seller",
        item_id="minecraft:ancient_debris",
        item_name="古代の残骸",
        item_count=2,
        price_xp=3_000,
        created_at="2026-08-18T00:00:00+00:00",
    )
    bot._rcon = AmbiguousMarketRcon()  # type: ignore[assignment]
    wallet_before = MinecraftXpWallet(5_000, 0, 5_000)
    wallet_after = MinecraftXpWallet(5_000, 3_000, 2_000)
    bot._level_bot_xp.request_market_purchase = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftMarketPurchaseRequest(
            status="reserved",
            message="購入を予約しました。",
            request_id=PURCHASE_ID,
            wallet_before=wallet_before,
            wallet_after=wallet_after,
        )
    )
    bot._level_bot_xp.update_market_purchase = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )

    result = asyncio.run(
        bot._purchase_market(
            guild_id=1001,
            listing_id=17,
            request_id=PURCHASE_ID,
            expected_price_xp=3_000,
            buyer=buyer,
        )
    )

    assert result is None
    bot._level_bot_xp.update_market_purchase.assert_not_awaited()  # type: ignore[attr-defined]
    listing = bot._market.get(17)
    assert listing is not None
    assert listing.status == "reserved"
    assert listing.purchase_request_id == PURCHASE_ID


def test_market_cancellation_persists_status_removes_card_and_delivers_log(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    seller = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Seller",
        server_player_name="Seller",
        discord_user_id=2002,
        discord_username="seller",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=SELLER_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id=LISTING_EVENT_ID,
        seller_account_id=seller.id,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="Seller",
        item_id="minecraft:ancient_debris",
        item_name="古代の残骸",
        item_count=2,
        price_xp=3_000,
        created_at="2026-08-18T00:00:00+00:00",
    )
    bot._rcon = MarketRcon()  # type: ignore[assignment]
    bot._refresh_market_listing = AsyncMock()  # type: ignore[method-assign]
    bot._deliver_market_cancellation_logs = AsyncMock()  # type: ignore[method-assign]

    result = asyncio.run(bot._cancel_market(listing_id=17, request_id=CANCEL_ID, seller=seller))

    assert result == "出品を取り消し、古代の残骸 x2を返却しました。"
    listing = bot._market.get(17)
    assert listing is not None and listing.status == "cancelled"
    assert listing.purchase_request_id == CANCEL_ID
    bot._refresh_market_listing.assert_awaited_once_with(17)  # type: ignore[attr-defined]
    bot._deliver_market_cancellation_logs.assert_awaited_once()  # type: ignore[attr-defined]
    rcon = bot._rcon
    assert isinstance(rcon, MarketRcon)
    assert f"usapo-event-bridge market-return 17 {SELLER_UUID} {CANCEL_ID}" in rcon.commands


def test_market_purchase_notifications_retry_only_failed_destination(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    seller = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Seller",
        server_player_name="Seller",
        discord_user_id=2002,
        discord_username="seller",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=SELLER_UUID,
    )
    buyer = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Buyer",
        server_player_name="Buyer",
        discord_user_id=2003,
        discord_username="buyer",
        source="self",
        status="active",
        created_by=2003,
        player_uuid=BUYER_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id=LISTING_EVENT_ID,
        seller_account_id=seller.id,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="Seller",
        item_id="minecraft:ancient_debris",
        item_name="古代の残骸",
        item_count=2,
        price_xp=3_000,
        created_at="2026-08-18T00:00:00+00:00",
    )
    reserved = bot._market.reserve_purchase(
        listing_id=17,
        request_id=PURCHASE_ID,
        expected_price_xp=3_000,
        buyer_account_id=buyer.id,
        buyer_discord_user_id=2003,
    )
    assert reserved is not None
    assert bot._market.set_status(17, PURCHASE_ID, "sold")
    bot._settings = RuntimeSettings(guild_id=1001)
    rcon = MarketRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    market_log_channel = AsyncMock()
    market_log_channel.send = AsyncMock(side_effect=[RuntimeError("Discord unavailable"), None])
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=market_log_channel
    )
    bot._send = AsyncMock()  # type: ignore[method-assign]

    async def exercise() -> tuple[bool, bool]:
        await bot._deliver_market_purchase_notifications()
        before_channel_configuration = bot._market.get(17)
        assert before_channel_configuration is not None
        state = (
            before_channel_configuration.minecraft_purchase_notified,
            before_channel_configuration.discord_purchase_notified,
        )
        bot._settings = RuntimeSettings(
            guild_id=1001,
            market_log_channel_id=777,
        )
        await bot._deliver_market_purchase_notifications()
        await bot._deliver_market_purchase_notifications()
        return state

    state_before_channel_configuration = asyncio.run(exercise())

    assert state_before_channel_configuration == (True, False)
    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 1
    assert market_log_channel.send.await_count == 2
    bot._send.assert_not_awaited()  # type: ignore[attr-defined]
    assert bot._resolve_and_validate_channel.await_count == 2  # type: ignore[attr-defined]
    listing = bot._market.get(17)
    assert listing is not None
    assert listing.minecraft_purchase_notified
    assert listing.discord_purchase_notified
    assert bot._market.list_pending_purchase_notifications() == []


def test_market_listing_log_is_acknowledged_only_after_successful_retry(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    milliseconds = int(datetime.now(UTC).timestamp() * 1_000)
    line = _line(
        100,
        f"USAPO_MARKET_LISTING|1|{LISTING_EVENT_ID}|17|{SELLER_UUID}|"
        f"{_encode('Seller')}|{_encode('minecraft:ancient_debris')}|"
        f"{_encode('古代の残骸')}|2|3000|{milliseconds}",
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]
    bot._handle_market_listing = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeError("temporary failure"), None]
    )

    with patch("mc_bot.bot.asyncio.sleep", new=AsyncMock()):
        asyncio.run(bot._forward_logs())

    assert bot._handle_market_listing.await_count == 2  # type: ignore[attr-defined]
    assert tailer.acknowledged == [line]
