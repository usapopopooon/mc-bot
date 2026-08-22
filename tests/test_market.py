import base64
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import discord
import pytest

from mc_bot.experience import MinecraftXpWallet
from mc_bot.market import (
    MarketStore,
    market_cancel_log_nonce,
    market_listing_embed,
    market_purchase_tellraw_command,
    market_transfer_command,
    parse_market_transfer_result,
)
from mc_bot.market_request import parse_market_listing, parse_market_request
from mc_bot.market_ui import market_balance_text, market_guide_embed, market_panel_embed
from mc_bot.translations import MinecraftItemTranslator

SELLER_UUID = "22222222-2222-4222-8222-222222222222"
BUYER_UUID = "33333333-3333-4333-8333-333333333333"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_store_migrates_existing_listings_to_purchase_notification_tracking(
    tmp_path,
) -> None:
    path = tmp_path / "accounts.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE minecraft_market_listings (
                listing_id INTEGER PRIMARY KEY CHECK (listing_id > 0),
                event_id TEXT NOT NULL UNIQUE,
                seller_account_id INTEGER NOT NULL,
                seller_discord_user_id INTEGER NOT NULL,
                seller_uuid TEXT NOT NULL,
                seller_name TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_count INTEGER NOT NULL CHECK (item_count > 0),
                price_xp INTEGER NOT NULL CHECK (price_xp > 0),
                status TEXT NOT NULL DEFAULT 'active' CHECK (
                    status IN ('active', 'reserved', 'sold', 'cancelling', 'cancelled')
                ),
                purchase_request_id TEXT UNIQUE,
                buyer_account_id INTEGER,
                buyer_discord_user_id INTEGER,
                discord_message_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO minecraft_market_listings VALUES (
                17,
                '11111111-1111-4111-8111-111111111111',
                2,
                2002,
                '22222222-2222-4222-8222-222222222222',
                'Seller',
                'minecraft:ancient_debris',
                '古代の残骸',
                2,
                3000,
                'sold',
                '44444444-4444-4444-8444-444444444444',
                3,
                2003,
                NULL,
                '2026-08-18T00:00:00+00:00',
                '2026-08-18T00:01:00+00:00'
            );
            INSERT INTO minecraft_market_listings VALUES (
                18,
                '55555555-5555-4555-8555-555555555555',
                2,
                2002,
                '22222222-2222-4222-8222-222222222222',
                'Seller',
                'minecraft:diamond',
                'ダイヤモンド',
                1,
                500,
                'cancelled',
                '66666666-6666-4666-8666-666666666666',
                NULL,
                NULL,
                901,
                '2026-08-18T00:00:00+00:00',
                '2026-08-18T00:01:00+00:00'
            );
            """
        )

    store = MarketStore(path)
    store.initialize()

    listing = store.get(17)
    assert listing is not None
    assert listing.status == "sold"
    assert not listing.minecraft_purchase_notified
    assert not listing.discord_purchase_notified
    assert store.list_pending_purchase_notifications() == [listing]
    historical_cancel = store.get(18)
    assert historical_cancel is not None
    assert historical_cancel.discord_cancel_log_notified
    assert store.list_cancelled_unnotified() == []


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


def test_listing_event_and_card_preserve_ordinary_item_enchantments(tmp_path) -> None:
    milliseconds = int(datetime(2026, 8, 18, tzinfo=UTC).timestamp() * 1_000)
    item_name = "トライデント（召雷 / 水生特効 V / 忠誠 III / 修繕 / 耐久力 III）"  # noqa: RUF001
    event = parse_market_listing(
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        f"USAPO_MARKET_LISTING|1|{REQUEST_ID}|17|{SELLER_UUID}|"
        f"{_encoded('.Yuki')}|{_encoded('minecraft:trident')}|"
        f"{_encoded(item_name)}|1|3000|{milliseconds}"
    )

    assert event is not None
    assert event.item_name == item_name

    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    listing, _ = store.add_listing(
        listing_id=event.listing_id,
        event_id=event.event_id,
        seller_account_id=2,
        seller_discord_user_id=2002,
        seller_uuid=event.seller_uuid,
        seller_name=event.seller_name,
        item_id=event.item_id,
        item_name=event.item_name,
        item_count=event.item_count,
        price_xp=event.price_xp,
        created_at=event.created_at,
    )

    assert market_listing_embed(listing).title == f"#17 {item_name} x1"


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
    store.set_discord_message(17, 901)
    assert store.set_status(
        17,
        "44444444-4444-4444-8444-444444444444",
        "sold",
    )
    assert [item.listing_id for item in store.list_sold_with_discord_message()] == [17]
    pending = store.list_pending_purchase_notifications()
    assert len(pending) == 1
    assert not pending[0].minecraft_purchase_notified
    assert not pending[0].discord_purchase_notified

    store.mark_purchase_notified(
        17,
        "44444444-4444-4444-8444-444444444444",
        "minecraft",
    )
    pending = store.list_pending_purchase_notifications()
    assert len(pending) == 1
    assert pending[0].minecraft_purchase_notified
    assert not pending[0].discord_purchase_notified

    store.mark_purchase_notified(
        17,
        "44444444-4444-4444-8444-444444444444",
        "discord",
    )
    assert store.list_pending_purchase_notifications() == []

    store.set_discord_message(17, None)

    assert store.list_sold_with_discord_message() == []
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


def test_store_tracks_cancelled_listing_log_delivery(tmp_path) -> None:
    store = MarketStore(tmp_path / "accounts.db")
    store.initialize()
    store.add_listing(
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
    store.set_discord_message(17, 901)
    cancel_id = "44444444-4444-4444-8444-444444444444"
    cancelling = store.begin_cancel(listing_id=17, seller_account_id=2, request_id=cancel_id)

    assert cancelling is not None
    assert store.set_status(17, cancel_id, "cancelled")
    cancelled = store.get(17)
    assert cancelled is not None
    assert [item.listing_id for item in store.list_cancelled_with_discord_message()] == [17]
    assert store.list_cancelled_unnotified() == [cancelled]

    store.mark_cancel_log_delivery_attempted(17, cancel_id)
    attempted = store.get(17)
    assert attempted is not None and attempted.discord_cancel_log_delivery_attempted
    store.mark_cancel_log_notified(17, cancel_id)

    notified = store.get(17)
    assert notified is not None and notified.discord_cancel_log_notified
    assert store.list_cancelled_unnotified() == []
    assert market_cancel_log_nonce(cancel_id) == market_cancel_log_nonce(cancel_id)
    assert 0 <= market_cancel_log_nonce(cancel_id) < 2**64


def test_store_refreshes_presentation_fields_without_weakening_listing_identity(
    tmp_path,
) -> None:
    store = MarketStore(tmp_path / "accounts.db")
    store.initialize()
    store.add_listing(
        listing_id=17,
        event_id=REQUEST_ID,
        seller_account_id=2,
        seller_discord_user_id=2002,
        seller_uuid=SELLER_UUID,
        seller_name="OldSeller",
        item_id="minecraft:ancient_debris",
        item_name="ancient debris",
        item_count=2,
        price_xp=3_000,
        created_at="2026-08-18T00:00:00+00:00",
    )
    store.set_discord_message(17, 901)

    refreshed, created = store.add_listing(
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

    assert not created
    assert refreshed.seller_name == "Seller"
    assert refreshed.item_name == "古代の残骸"
    assert refreshed.discord_message_id == 901
    with pytest.raises(ValueError, match="idempotency conflict"):
        store.add_listing(
            listing_id=17,
            event_id=REQUEST_ID,
            seller_account_id=2,
            seller_discord_user_id=2002,
            seller_uuid=SELLER_UUID,
            seller_name="Seller",
            item_id="minecraft:ancient_debris",
            item_name="古代の残骸",
            item_count=2,
            price_xp=3_001,
            created_at="2026-08-18T00:00:00+00:00",
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


def test_market_transfer_result_accepts_rcon_line_ending() -> None:
    result = parse_market_transfer_result(
        f"USAPO_MARKET_TRANSFER_RESULT|1|{REQUEST_ID}|17|completed|sold|duplicate\n",
        request_id=REQUEST_ID,
        listing_id=17,
    )

    assert result.status == "completed"
    assert result.listing_status == "sold"
    assert result.duplicate


def test_market_purchase_tellraw_contains_exact_transaction_parties_and_values() -> None:
    command = market_purchase_tellraw_command(
        server_name="うさぽサーバー",
        buyer_name="Buyer",
        seller_name="Seller",
        item_name="古代の残骸",
        item_count=2,
        price_xp=3_000,
    )

    assert command.startswith("tellraw @a ")
    text = "".join(
        component["text"] for component in json.loads(command.removeprefix("tellraw @a "))
    )
    assert text == (
        "🛒 [うさぽサーバー] BuyerさんがSellerさんから古代の残骸 x2を3,000サーバーXPで購入しました!"
    )


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


def test_listing_embed_keeps_details_concise(tmp_path) -> None:
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
    assert embed.title == "#1 ダイヤモンド x3"
    assert "720 XP" in (embed.description or "")
    assert embed.footer.text is None

    custom_name_embed = market_listing_embed(replace(listing, item_name="Yukiのダイヤモンド"))
    assert custom_name_embed.title == "#1 Yukiのダイヤモンド x3"


def test_item_translator_uses_minecraft_26_2_names_and_safe_fallbacks() -> None:
    translator = MinecraftItemTranslator.load()

    assert len(translator) >= 2_700
    assert translator.translate("minecraft:ancient_debris", "ancient debris") == "古代の残骸"
    assert translator.translate("minecraft:diamond", "Yukiのダイヤ") == "Yukiのダイヤ"
    assert translator.translate("minecraft:future_item", "future item") == "future item"


def test_market_panel_keeps_summary_short_and_moves_details_to_guide() -> None:
    panel = market_panel_embed()
    guide = market_guide_embed()

    assert panel.title == "Minecraft フリマ"
    assert panel.description is not None
    assert panel.description == (
        "どんなアイテムでもサーバーXPで出品・購入できます。\n詳しくは「使い方」を押してください。"
    )
    assert "サーバーXP" in panel.description
    assert "/market sell" not in panel.description
    assert "Java版・Bedrock版とも `/market`" in str(guide.to_dict())
    assert "商品を見る" in str(guide.to_dict())
    assert "手に持ったスタックを出品" in str(guide.to_dict())
    assert "サーバーXP残高" in str(guide.to_dict())
    assert "/market sell" in str(guide.to_dict())
    assert "/market list [ページ]" in str(guide.to_dict())
    assert "/market balance" in str(guide.to_dict())
    assert "Bedrock版" in str(guide.to_dict())
    assert "商品カード" in str(guide.to_dict())
    assert "サーバーXP" in str(guide.to_dict())
    assert "オンライン" in str(guide.to_dict())


def test_market_panel_balance_identifies_server_xp() -> None:
    text = market_balance_text(
        MinecraftXpWallet(total_xp=5_000, spent_xp=1_000, available_xp=4_000)
    )

    assert "現在のサーバーXP: **4,000 XP**" in text
