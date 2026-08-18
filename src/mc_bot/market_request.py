from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from mc_bot.player_names import is_safe_server_player_name

_LISTING_PREFIX = "[UsapoEventBridge] USAPO_MARKET_LISTING|1|"
_REQUEST_PREFIX = "[UsapoEventBridge] USAPO_MARKET_REQUEST|1|"


@dataclass(frozen=True, slots=True)
class MinecraftMarketListingEvent:
    event_id: str
    listing_id: int
    seller_uuid: str
    seller_name: str
    item_id: str
    item_name: str
    item_count: int
    price_xp: int
    created_at: str


@dataclass(frozen=True, slots=True)
class MinecraftMarketRequest:
    request_id: str
    kind: Literal["buy", "cancel", "balance"]
    listing_id: int
    player_uuid: str
    player_name: str
    expected_price_xp: int
    requested_at: str


def parse_market_listing(line: str) -> MinecraftMarketListingEvent | None:
    message = _message(line)
    if message is None or not message.startswith(_LISTING_PREFIX):
        return None
    fields = message.removeprefix(_LISTING_PREFIX).split("|")
    if len(fields) != 9:
        raise ValueError("Minecraft market listing has an invalid field count")
    try:
        event_id = str(uuid.UUID(fields[0]))
        listing_id = int(fields[1])
        seller_uuid = str(uuid.UUID(fields[2]))
        seller_name = _decode(fields[3])
        item_id = _decode(fields[4])
        item_name = _decode(fields[5])
        item_count = int(fields[6])
        price_xp = int(fields[7])
        milliseconds = int(fields[8])
        created_at = datetime.fromtimestamp(milliseconds / 1_000, UTC).isoformat()
    except (ValueError, UnicodeDecodeError, binascii.Error, OverflowError, OSError) as error:
        raise ValueError("Minecraft market listing contains an invalid value") from error
    if (
        listing_id <= 0
        or not is_safe_server_player_name(seller_name)
        or not item_id.startswith("minecraft:")
        or not item_name.strip()
        or not 1 <= item_count <= 99
        or price_xp <= 0
        or milliseconds < 0
    ):
        raise ValueError("Minecraft market listing contains invalid item data")
    return MinecraftMarketListingEvent(
        event_id=event_id,
        listing_id=listing_id,
        seller_uuid=seller_uuid,
        seller_name=seller_name,
        item_id=item_id,
        item_name=item_name[:120],
        item_count=item_count,
        price_xp=price_xp,
        created_at=created_at,
    )


def parse_market_request(line: str) -> MinecraftMarketRequest | None:
    message = _message(line)
    if message is None or not message.startswith(_REQUEST_PREFIX):
        return None
    fields = message.removeprefix(_REQUEST_PREFIX).split("|")
    if len(fields) != 7:
        raise ValueError("Minecraft market request has an invalid field count")
    try:
        request_id = str(uuid.UUID(fields[0]))
        if fields[1] not in {"buy", "cancel", "balance"}:
            raise ValueError("unknown market request kind")
        kind = cast("Literal['buy', 'cancel', 'balance']", fields[1])
        listing_id = int(fields[2])
        player_uuid = str(uuid.UUID(fields[3]))
        player_name = _decode(fields[4])
        expected_price_xp = int(fields[5])
        milliseconds = int(fields[6])
        requested_at = datetime.fromtimestamp(milliseconds / 1_000, UTC).isoformat()
    except (ValueError, UnicodeDecodeError, binascii.Error, OverflowError, OSError) as error:
        raise ValueError("Minecraft market request contains an invalid value") from error
    valid_selection = (
        listing_id == expected_price_xp == 0
        if kind == "balance"
        else listing_id > 0 and expected_price_xp > 0
    )
    if not valid_selection or not is_safe_server_player_name(player_name) or milliseconds < 0:
        raise ValueError("Minecraft market request contains invalid player or listing data")
    return MinecraftMarketRequest(
        request_id=request_id,
        kind=kind,
        listing_id=listing_id,
        player_uuid=player_uuid,
        player_name=player_name,
        expected_price_xp=expected_price_xp,
        requested_at=requested_at,
    )


def _message(line: str) -> str | None:
    _, separator, message = line.partition("]: ")
    return message if separator else None


def _decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True).decode("utf-8")


__all__ = [
    "MinecraftMarketListingEvent",
    "MinecraftMarketRequest",
    "parse_market_listing",
    "parse_market_request",
]
