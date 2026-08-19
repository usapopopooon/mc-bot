from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import discord

from mc_bot.translations import MinecraftItemTranslator

_ITEM_TRANSLATOR = MinecraftItemTranslator.load()

type MarketTransferStatus = Literal[
    "completed",
    "unavailable",
    "recipient_mismatch",
    "player_offline",
    "inventory_full",
    "storage_error",
]
type MarketTransferListingStatus = Literal["active", "delivering", "sold", "cancelled", "unknown"]


@dataclass(frozen=True, slots=True)
class MarketListing:
    listing_id: int
    event_id: str
    seller_account_id: int
    seller_discord_user_id: int
    seller_uuid: str
    seller_name: str
    item_id: str
    item_name: str
    item_count: int
    price_xp: int
    status: str
    purchase_request_id: str | None
    buyer_account_id: int | None
    buyer_discord_user_id: int | None
    discord_message_id: int | None
    created_at: str
    updated_at: str
    minecraft_purchase_notified: bool = False
    discord_purchase_notified: bool = False

    @property
    def display_item_name(self) -> str:
        return _ITEM_TRANSLATOR.translate(self.item_id, self.item_name)


@dataclass(frozen=True, slots=True)
class MarketTransferResult:
    status: MarketTransferStatus
    listing_status: MarketTransferListingStatus
    duplicate: bool

    @property
    def delivery_recorded(self) -> bool:
        return self.status == "completed" or self.duplicate


class MarketStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS minecraft_market_listings (
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
                    updated_at TEXT NOT NULL,
                    minecraft_purchase_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_purchase_notified IN (0, 1)),
                    discord_purchase_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (discord_purchase_notified IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS minecraft_market_listing_status
                    ON minecraft_market_listings(status, listing_id);
                CREATE INDEX IF NOT EXISTS minecraft_market_listing_seller
                    ON minecraft_market_listings(seller_account_id, status);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(minecraft_market_listings)"
                ).fetchall()
            }
            for column in (
                "minecraft_purchase_notified",
                "discord_purchase_notified",
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE minecraft_market_listings ADD COLUMN {column} "
                        "INTEGER NOT NULL DEFAULT 0 CHECK ("
                        f"{column} IN (0, 1))"
                    )

    def add_listing(
        self,
        *,
        listing_id: int,
        event_id: str,
        seller_account_id: int,
        seller_discord_user_id: int,
        seller_uuid: str,
        seller_name: str,
        item_id: str,
        item_name: str,
        item_count: int,
        price_xp: int,
        created_at: str,
    ) -> tuple[MarketListing, bool]:
        normalized_event = str(uuid.UUID(event_id))
        normalized_uuid = str(uuid.UUID(seller_uuid))
        if min(listing_id, seller_account_id, seller_discord_user_id, item_count, price_xp) <= 0:
            raise ValueError("invalid market listing")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM minecraft_market_listings WHERE listing_id = ? OR event_id = ?",
                (listing_id, normalized_event),
            ).fetchone()
            if existing is not None:
                listing = _listing(existing)
                same_listing = (
                    listing.listing_id == listing_id
                    and listing.event_id == normalized_event
                    and listing.seller_account_id == seller_account_id
                    and listing.seller_discord_user_id == seller_discord_user_id
                    and listing.seller_uuid == normalized_uuid
                    and listing.item_id == item_id
                    and listing.item_count == item_count
                    and listing.price_xp == price_xp
                )
                if not same_listing:
                    raise ValueError("market listing idempotency conflict")
                if listing.seller_name != seller_name or listing.item_name != item_name:
                    connection.execute(
                        """
                        UPDATE minecraft_market_listings
                        SET seller_name = ?, item_name = ?, updated_at = ?
                        WHERE listing_id = ?
                        """,
                        (seller_name, item_name, _now(), listing_id),
                    )
                    updated = connection.execute(
                        "SELECT * FROM minecraft_market_listings WHERE listing_id = ?",
                        (listing_id,),
                    ).fetchone()
                    assert updated is not None
                    listing = _listing(updated)
                return listing, False
            connection.execute(
                """
                INSERT INTO minecraft_market_listings (
                    listing_id, event_id, seller_account_id, seller_discord_user_id,
                    seller_uuid, seller_name, item_id, item_name, item_count,
                    price_xp, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    listing_id,
                    normalized_event,
                    seller_account_id,
                    seller_discord_user_id,
                    normalized_uuid,
                    seller_name,
                    item_id,
                    item_name,
                    item_count,
                    price_xp,
                    created_at,
                    _now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM minecraft_market_listings WHERE listing_id = ?",
                (listing_id,),
            ).fetchone()
            assert row is not None
            return _listing(row), True

    def get(self, listing_id: int) -> MarketListing | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM minecraft_market_listings WHERE listing_id = ?",
                (listing_id,),
            ).fetchone()
        return _listing(row) if row is not None else None

    def list_open(self) -> list[MarketListing]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_market_listings
                WHERE status IN ('active', 'reserved', 'cancelling')
                ORDER BY listing_id DESC
                """
            ).fetchall()
        return [_listing(row) for row in rows]

    def list_sold_with_discord_message(self) -> list[MarketListing]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_market_listings
                WHERE status = 'sold' AND discord_message_id IS NOT NULL
                ORDER BY listing_id
                """
            ).fetchall()
        return [_listing(row) for row in rows]

    def list_pending_purchase_notifications(self) -> list[MarketListing]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_market_listings
                WHERE status = 'sold'
                  AND purchase_request_id IS NOT NULL
                  AND buyer_account_id IS NOT NULL
                  AND buyer_discord_user_id IS NOT NULL
                  AND (
                    minecraft_purchase_notified = 0
                    OR discord_purchase_notified = 0
                  )
                ORDER BY updated_at, listing_id
                """
            ).fetchall()
        return [_listing(row) for row in rows]

    def mark_purchase_notified(
        self,
        listing_id: int,
        request_id: str,
        destination: str,
    ) -> None:
        column = {
            "minecraft": "minecraft_purchase_notified",
            "discord": "discord_purchase_notified",
        }.get(destination)
        if column is None:
            raise ValueError("unknown market purchase notification destination")
        normalized_request = str(uuid.UUID(request_id))
        with self._connect() as connection:
            connection.execute(
                f"UPDATE minecraft_market_listings SET {column} = 1, updated_at = ? "
                "WHERE listing_id = ? AND purchase_request_id = ? AND status = 'sold'",
                (_now(), listing_id, normalized_request),
            )

    def reserve_purchase(
        self,
        *,
        listing_id: int,
        request_id: str,
        expected_price_xp: int,
        buyer_account_id: int,
        buyer_discord_user_id: int,
    ) -> MarketListing | None:
        normalized_request = str(uuid.UUID(request_id))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM minecraft_market_listings WHERE listing_id = ?",
                (listing_id,),
            ).fetchone()
            if row is None:
                return None
            listing = _listing(row)
            if listing.purchase_request_id == normalized_request:
                if (
                    listing.status in {"reserved", "sold"}
                    and listing.buyer_account_id == buyer_account_id
                    and listing.buyer_discord_user_id == buyer_discord_user_id
                    and listing.price_xp == expected_price_xp
                ):
                    return listing
                return None
            if (
                listing.status == "reserved"
                and listing.buyer_account_id == buyer_account_id
                and listing.buyer_discord_user_id == buyer_discord_user_id
                and listing.price_xp == expected_price_xp
            ):
                return listing
            if (
                listing.status != "active"
                or listing.price_xp != expected_price_xp
                or listing.seller_account_id == buyer_account_id
                or listing.seller_discord_user_id == buyer_discord_user_id
            ):
                return None
            connection.execute(
                """
                UPDATE minecraft_market_listings
                SET status = 'reserved', purchase_request_id = ?,
                    buyer_account_id = ?, buyer_discord_user_id = ?, updated_at = ?
                WHERE listing_id = ? AND status = 'active' AND price_xp = ?
                """,
                (
                    normalized_request,
                    buyer_account_id,
                    buyer_discord_user_id,
                    _now(),
                    listing_id,
                    expected_price_xp,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM minecraft_market_listings WHERE listing_id = ?",
                (listing_id,),
            ).fetchone()
            return _listing(updated) if updated is not None else None

    def begin_cancel(
        self, *, listing_id: int, seller_account_id: int, request_id: str
    ) -> MarketListing | None:
        normalized_request = str(uuid.UUID(request_id))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE minecraft_market_listings
                SET status = 'cancelling', purchase_request_id = ?, updated_at = ?
                WHERE listing_id = ? AND seller_account_id = ? AND status = 'active'
                """,
                (normalized_request, _now(), listing_id, seller_account_id),
            )
            row = connection.execute(
                "SELECT * FROM minecraft_market_listings WHERE listing_id = ?",
                (listing_id,),
            ).fetchone()
            if row is None:
                return None
            listing = _listing(row)
            if changed.rowcount or (
                listing.status == "cancelling" and listing.seller_account_id == seller_account_id
            ):
                return listing
            return None

    def set_status(self, listing_id: int, request_id: str, status: str) -> bool:
        if status not in {"active", "sold", "cancelled"}:
            raise ValueError("invalid market listing status")
        normalized_request = str(uuid.UUID(request_id))
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE minecraft_market_listings
                SET status = ?, updated_at = ?
                WHERE listing_id = ? AND purchase_request_id = ?
                """,
                (status, _now(), listing_id, normalized_request),
            )
            return bool(result.rowcount)

    def set_discord_message(self, listing_id: int, message_id: int | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_market_listings
                SET discord_message_id = ?, updated_at = ? WHERE listing_id = ?
                """,
                (message_id, _now(), listing_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection


def market_transfer_command(
    action: str, listing_id: int, recipient_uuid: str, request_id: str
) -> str:
    if action not in {"deliver", "return"} or listing_id <= 0:
        raise ValueError("invalid market transfer")
    recipient = str(uuid.UUID(recipient_uuid))
    request = str(uuid.UUID(request_id))
    return f"usapo-event-bridge market-{action} {listing_id} {recipient} {request}"


def market_purchase_tellraw_command(
    *,
    server_name: str,
    buyer_name: str,
    seller_name: str,
    item_name: str,
    item_count: int,
    price_xp: int,
) -> str:
    if not all(value.strip() for value in (server_name, buyer_name, seller_name, item_name)):
        raise ValueError("market purchase notification values must not be blank")
    if item_count <= 0 or price_xp <= 0:
        raise ValueError("market purchase notification amounts must be positive")
    components = [
        {"text": "🛒 ["},
        {"text": server_name, "color": "aqua"},
        {"text": "] "},
        {"text": buyer_name, "color": "yellow"},
        {"text": "さんが"},
        {"text": seller_name, "color": "gold"},
        {"text": "さんから"},
        {"text": f"{item_name} x{item_count:,}", "color": "aqua", "bold": True},
        {"text": "を"},
        {"text": f"{price_xp:,}サーバーXP", "color": "green", "bold": True},
        {"text": "で購入しました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def parse_market_transfer_result(
    response: str, *, request_id: str, listing_id: int
) -> MarketTransferResult:
    prefix = "USAPO_MARKET_TRANSFER_RESULT|1|"
    marker = response.find(prefix)
    if marker < 0:
        raise ValueError("market transfer result is missing")
    fields = response[marker + len(prefix) :].split("|")
    if len(fields) != 5:
        raise ValueError("market transfer result is malformed")
    if str(uuid.UUID(fields[0])) != str(uuid.UUID(request_id)) or int(fields[1]) != listing_id:
        raise ValueError("market transfer result does not match request")
    statuses = {
        "completed",
        "unavailable",
        "recipient_mismatch",
        "player_offline",
        "inventory_full",
        "storage_error",
    }
    listing_statuses = {"active", "delivering", "sold", "cancelled", "unknown"}
    duplicate_values = {"new": False, "duplicate": True}
    if (
        fields[2] not in statuses
        or fields[3] not in listing_statuses
        or fields[4] not in duplicate_values
    ):
        raise ValueError("market transfer result contains an unknown state")
    return MarketTransferResult(
        status=cast("MarketTransferStatus", fields[2]),
        listing_status=cast("MarketTransferListingStatus", fields[3]),
        duplicate=duplicate_values[fields[4]],
    )


def market_listing_embed(listing: MarketListing) -> discord.Embed:
    status = {
        "active": "出品中",
        "reserved": "取引中",
        "sold": "売り切れ",
        "cancelling": "返却中",
        "cancelled": "出品取消",
    }[listing.status]
    color = discord.Color.green() if listing.status == "active" else discord.Color.dark_grey()
    embed = discord.Embed(
        title=f"#{listing.listing_id} {listing.display_item_name} x{listing.item_count}",
        description=f"**{listing.price_xp:,} XP**　{status}",
        color=color,
    )
    embed.add_field(name="出品者", value=f"<@{listing.seller_discord_user_id}>")
    embed.add_field(name="Minecraft", value=listing.seller_name)
    return embed


def _listing(row: sqlite3.Row) -> MarketListing:
    return MarketListing(
        listing_id=int(row["listing_id"]),
        event_id=str(row["event_id"]),
        seller_account_id=int(row["seller_account_id"]),
        seller_discord_user_id=int(row["seller_discord_user_id"]),
        seller_uuid=str(row["seller_uuid"]),
        seller_name=str(row["seller_name"]),
        item_id=str(row["item_id"]),
        item_name=str(row["item_name"]),
        item_count=int(row["item_count"]),
        price_xp=int(row["price_xp"]),
        status=str(row["status"]),
        purchase_request_id=row["purchase_request_id"],
        buyer_account_id=row["buyer_account_id"],
        buyer_discord_user_id=row["buyer_discord_user_id"],
        discord_message_id=row["discord_message_id"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        minecraft_purchase_notified=bool(row["minecraft_purchase_notified"]),
        discord_purchase_notified=bool(row["discord_purchase_notified"]),
    )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
