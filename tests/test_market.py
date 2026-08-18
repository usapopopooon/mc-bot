import base64
from datetime import UTC, datetime

import discord
import pytest

from mc_bot.market import (
    MarketStore,
    market_listing_embed,
    market_transfer_command,
    parse_market_transfer_result,
)
from mc_bot.market_request import parse_market_listing, parse_market_request

SELLER_UUID = "22222222-2222-4222-8222-222222222222"
BUYER_UUID = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_parses_versioned_listing_and_request_with_exact_price() -> None:
    milliseconds = int(datetime(2026, 8, 18, tzinfo=UTC).timestamp() * 1_000)
    listing = parse_market_listing(
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        f"USAPO_MARKET_LISTING|1|{REQUEST_ID}|17|{SELLER_UUID}|"
        f"{_encoded('.Yuki')}|{_encoded('minecraft:ancient_debris')}|"
        f"{_encoded('古代の残骸')}|2|3000|{milliseconds}"
    )
    request = parse_market_request(
        "[08:24:01] [Server thread/INFO]: [UsapoEventBridge] "
        f"USAPO_MARKET_REQUEST|1|{REQUEST_ID}|buy|17|{BUYER_UUID}|"
        f"{_encoded('Steve')}|3000|{milliseconds}"
    )

    assert listing is not None
    assert listing.listing_id == 17
    assert listing.item_id == "minecraft:ancient_debris"
    assert listing.item_name == "古代の残骸"
    assert listing.item_count == 2
    assert listing.price_xp == 3_000
    assert request is not None
    assert request.listing_id == listing.listing_id
    assert request.expected_price_xp == listing.price_xp


def test_rejects_tampered_balance_and_invalid_item_namespace() -> None:
    milliseconds = int(datetime.now(UTC).timestamp() * 1_000)
    with pytest.raises(ValueError):
        parse_market_request(
            "[08:24:01] [Server thread/INFO]: [UsapoEventBridge] "
            f"USAPO_MARKET_REQUEST|1|{REQUEST_ID}|balance|17|{BUYER_UUID}|"
            f"{_encoded('Steve')}|3000|{milliseconds}"
        )
    with pytest.raises(ValueError):
        parse_market_listing(
            "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
            f"USAPO_MARKET_LISTING|1|{REQUEST_ID}|17|{SELLER_UUID}|"
            f"{_encoded('Seller')}|{_encoded('command:block')}|{_encoded('item')}|"
            f"1|10|{milliseconds}"
        )


def test_store_serializes_purchase_and_preserves_party_mapping(tmp_path) -> None:
    store = MarketStore(tmp_path / "accounts.db")
    store.initialize()
    listing, created = store.add_listing(
        listing_id=17,
        event_id=REQUEST_ID,
        seller_account_id=2,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="Seller",
        item_id="minecraft:ancient_debris",
        item_name="古代の残骸",
        item_count=2,
        price_xp=3_000,
        created_at="2026-08-18T00:00:00+00:00",
    )
    duplicate, duplicate_created = store.add_listing(
        listing_id=17,
        event_id=REQUEST_ID,
        seller_account_id=2,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="Seller",
        item_id="minecraft:ancient_debris",
        item_name="古代の残骸",
        item_count=2,
        price_xp=3_000,
        created_at="2026-08-18T00:00:00+00:00",
    )
    reserved = store.reserve_purchase(
        listing_id=17,
        request_id="44444444-4444-4444-8444-444444444444",
        expected_price_xp=3_000,
        buyer_account_id=3,
        buyer_discord_user_id=2003,
    )

    assert created
    assert not duplicate_created
    assert duplicate == listing
    assert reserved is not None
    assert reserved.seller_account_id == 2
    assert reserved.seller_discord_user_id == 2002
    assert reserved.buyer_account_id == 3
    assert reserved.buyer_discord_user_id == 2003
    assert (
        store.reserve_purchase(
            listing_id=17,
            request_id="44444444-4444-4444-8444-444444444444",
            expected_price_xp=3_000,
            buyer_account_id=4,
            buyer_discord_user_id=2004,
        )
        is None
    )
    assert (
        store.reserve_purchase(
            listing_id=17,
            request_id="55555555-5555-4555-8555-555555555555",
            expected_price_xp=3_000,
            buyer_account_id=4,
            buyer_discord_user_id=2004,
        )
        is None
    )


def test_market_rcon_protocol_and_card() -> None:
    command = market_transfer_command("deliver", 17, BUYER_UUID, REQUEST_ID)
    result = parse_market_transfer_result(
        f"USAPO_MARKET_TRANSFER_RESULT|1|{REQUEST_ID}|17|completed|sold|new",
        request_id=REQUEST_ID,
        listing_id=17,
    )
    store_listing = MarketStore  # keep type imports exercised without runtime globals

    assert command == (f"usapo-event-bridge market-deliver 17 {BUYER_UUID} {REQUEST_ID}")
    assert result.status == "completed"
    assert result.listing_status == "sold"
    assert not result.duplicate
    assert result.delivery_recorded
    assert store_listing is MarketStore


def test_market_transfer_result_preserves_ambiguous_recorded_delivery() -> None:
    result = parse_market_transfer_result(
        f"USAPO_MARKET_TRANSFER_RESULT|1|{REQUEST_ID}|17|storage_error|delivering|duplicate",
        request_id=REQUEST_ID,
        listing_id=17,
    )

    assert result.status == "storage_error"
    assert result.listing_status == "delivering"
    assert result.duplicate
    assert result.delivery_recorded


def test_listing_embed_shows_no_fee_market(tmp_path) -> None:
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    listing, _ = store.add_listing(
        listing_id=1,
        event_id=REQUEST_ID,
        seller_account_id=2,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="Seller",
        item_id="minecraft:diamond",
        item_name="diamond",
        item_count=3,
        price_xp=720,
        created_at="2026-08-18T00:00:00+00:00",
    )

    embed = market_listing_embed(listing)

    assert isinstance(embed, discord.Embed)
    assert embed.title == "#1 diamond x3"
    assert "720 XP" in (embed.description or "")
    assert "手数料なし" in (embed.footer.text or "")
