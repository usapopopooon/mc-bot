from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from mc_bot.player_names import is_safe_server_player_name

_EVENT_PREFIXES = {
    1: "[UsapoEventBridge] USAPO_EMERALD_EXCHANGE|1|",
    2: "[UsapoEventBridge] USAPO_EMERALD_EXCHANGE|2|",
}
_RESULT_PREFIX = "USAPO_EMERALD_EXCHANGE_RESULT|2|"
_DIAMOND_EVENT_PREFIX = "[UsapoEventBridge] USAPO_DIAMOND_EXCHANGE|1|"
_DIAMOND_RESULT_PREFIX = "USAPO_DIAMOND_EXCHANGE_RESULT|1|"
_ALLOWED_EMERALD_COUNTS = frozenset({32, 64})
_ALLOWED_DIAMOND_COUNTS = frozenset({1, 4})
_RESULT_STATUSES = frozenset(
    {"completed", "insufficient_emeralds", "inventory_full", "player_offline"}
)
_DIAMOND_RESULT_STATUSES = frozenset(
    {"completed", "insufficient_diamonds", "inventory_full", "player_offline"}
)


@dataclass(frozen=True, slots=True)
class EmeraldDiamondExchangeResult:
    request_id: str
    status: str
    emerald_count: int
    diamond_count: int
    duplicate: bool


@dataclass(frozen=True, slots=True)
class EmeraldDiamondExchangeEvent:
    request_id: str
    player_uuid: str
    player_name: str
    emerald_count: int
    diamond_count: int
    occurred_at: str


@dataclass(frozen=True, slots=True)
class DiamondEmeraldExchangeResult:
    request_id: str
    status: str
    diamond_count: int
    emerald_count: int
    duplicate: bool


@dataclass(frozen=True, slots=True)
class DiamondEmeraldExchangeEvent:
    request_id: str
    player_uuid: str
    player_name: str
    diamond_count: int
    emerald_count: int
    occurred_at: str


def emerald_diamond_exchange_command(player_uuid: str, emerald_count: int, request_id: str) -> str:
    normalized_player_uuid = str(uuid.UUID(player_uuid))
    normalized_request_id = str(uuid.UUID(request_id))
    if emerald_count not in _ALLOWED_EMERALD_COUNTS:
        raise ValueError("emerald_count must be 32 or 64")
    return (
        "usapo-event-bridge emerald-diamond-v2 "
        f"{normalized_player_uuid} {emerald_count} {normalized_request_id}"
    )


def diamond_emerald_exchange_command(player_uuid: str, diamond_count: int, request_id: str) -> str:
    normalized_player_uuid = str(uuid.UUID(player_uuid))
    normalized_request_id = str(uuid.UUID(request_id))
    if diamond_count not in _ALLOWED_DIAMOND_COUNTS:
        raise ValueError("diamond_count must be 1 or 4")
    return (
        "usapo-event-bridge diamond-emerald-v1 "
        f"{normalized_player_uuid} {diamond_count} {normalized_request_id}"
    )


def parse_emerald_diamond_exchange_result(
    response: str,
    *,
    expected_request_id: str,
    expected_emerald_count: int,
) -> EmeraldDiamondExchangeResult:
    fields = response.strip().removeprefix(_RESULT_PREFIX).split("|")
    if not response.strip().startswith(_RESULT_PREFIX) or len(fields) != 5:
        raise ValueError("Minecraft returned an invalid emerald exchange result")
    request_id_text, status, emeralds_text, diamonds_text, disposition = fields
    try:
        request_id = str(uuid.UUID(request_id_text))
        expected = str(uuid.UUID(expected_request_id))
        emerald_count = int(emeralds_text)
        diamond_count = int(diamonds_text)
    except ValueError as error:
        raise ValueError("Minecraft returned an invalid emerald exchange result") from error
    if (
        request_id != expected
        or status not in _RESULT_STATUSES
        or emerald_count not in _ALLOWED_EMERALD_COUNTS
        or emerald_count != expected_emerald_count
        or diamond_count != emerald_count // 32
        or disposition not in {"new", "duplicate"}
        or (status != "completed" and disposition == "duplicate")
    ):
        raise ValueError("Minecraft returned an invalid emerald exchange result")
    return EmeraldDiamondExchangeResult(
        request_id=request_id,
        status=status,
        emerald_count=emerald_count,
        diamond_count=diamond_count,
        duplicate=disposition == "duplicate",
    )


def parse_diamond_emerald_exchange_result(
    response: str,
    *,
    expected_request_id: str,
    expected_diamond_count: int,
) -> DiamondEmeraldExchangeResult:
    fields = response.strip().removeprefix(_DIAMOND_RESULT_PREFIX).split("|")
    if not response.strip().startswith(_DIAMOND_RESULT_PREFIX) or len(fields) != 5:
        raise ValueError("Minecraft returned an invalid diamond exchange result")
    request_id_text, status, diamonds_text, emeralds_text, disposition = fields
    try:
        request_id = str(uuid.UUID(request_id_text))
        expected = str(uuid.UUID(expected_request_id))
        diamond_count = int(diamonds_text)
        emerald_count = int(emeralds_text)
    except ValueError as error:
        raise ValueError("Minecraft returned an invalid diamond exchange result") from error
    if (
        request_id != expected
        or status not in _DIAMOND_RESULT_STATUSES
        or diamond_count not in _ALLOWED_DIAMOND_COUNTS
        or diamond_count != expected_diamond_count
        or emerald_count != diamond_count * 16
        or disposition not in {"new", "duplicate"}
        or (status != "completed" and disposition == "duplicate")
    ):
        raise ValueError("Minecraft returned an invalid diamond exchange result")
    return DiamondEmeraldExchangeResult(
        request_id=request_id,
        status=status,
        diamond_count=diamond_count,
        emerald_count=emerald_count,
        duplicate=disposition == "duplicate",
    )


def parse_emerald_diamond_exchange_event(
    line: str,
) -> EmeraldDiamondExchangeEvent | None:
    _, separator, message = line.partition("]: ")
    if not separator:
        return None
    version = next(
        (version for version, prefix in _EVENT_PREFIXES.items() if message.startswith(prefix)),
        None,
    )
    if version is None:
        return None
    fields = message.removeprefix(_EVENT_PREFIXES[version]).split("|")
    if len(fields) != 6:
        raise ValueError("Minecraft emerald exchange event has an invalid field count")
    request_id_text, player_uuid_text, encoded_name, emeralds_text, diamonds_text, millis_text = (
        fields
    )
    try:
        request_id = str(uuid.UUID(request_id_text))
        player_uuid = str(uuid.UUID(player_uuid_text))
        padding = "=" * (-len(encoded_name) % 4)
        player_name = base64.b64decode(
            encoded_name + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        emerald_count = int(emeralds_text)
        diamond_count = int(diamonds_text)
        milliseconds = int(millis_text)
        occurred_at = datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        OverflowError,
        OSError,
    ) as error:
        raise ValueError("Minecraft emerald exchange event contains an invalid value") from error
    if (
        not is_safe_server_player_name(player_name)
        or (
            version == 1
            and (emerald_count not in {16, 32, 64} or diamond_count != emerald_count // 16)
        )
        or (
            version == 2
            and (
                emerald_count not in _ALLOWED_EMERALD_COUNTS or diamond_count != emerald_count // 32
            )
        )
        or milliseconds < 0
    ):
        raise ValueError("Minecraft emerald exchange event contains an invalid value")
    return EmeraldDiamondExchangeEvent(
        request_id=request_id,
        player_uuid=player_uuid,
        player_name=player_name,
        emerald_count=emerald_count,
        diamond_count=diamond_count,
        occurred_at=occurred_at,
    )


def parse_diamond_emerald_exchange_event(
    line: str,
) -> DiamondEmeraldExchangeEvent | None:
    _, separator, message = line.partition("]: ")
    if not separator or not message.startswith(_DIAMOND_EVENT_PREFIX):
        return None
    fields = message.removeprefix(_DIAMOND_EVENT_PREFIX).split("|")
    if len(fields) != 6:
        raise ValueError("Minecraft diamond exchange event has an invalid field count")
    request_id_text, player_uuid_text, encoded_name, diamonds_text, emeralds_text, millis_text = (
        fields
    )
    try:
        request_id = str(uuid.UUID(request_id_text))
        player_uuid = str(uuid.UUID(player_uuid_text))
        padding = "=" * (-len(encoded_name) % 4)
        player_name = base64.b64decode(
            encoded_name + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        diamond_count = int(diamonds_text)
        emerald_count = int(emeralds_text)
        milliseconds = int(millis_text)
        occurred_at = datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        OverflowError,
        OSError,
    ) as error:
        raise ValueError("Minecraft diamond exchange event contains an invalid value") from error
    if (
        not is_safe_server_player_name(player_name)
        or diamond_count not in _ALLOWED_DIAMOND_COUNTS
        or emerald_count != diamond_count * 16
        or milliseconds < 0
    ):
        raise ValueError("Minecraft diamond exchange event contains an invalid value")
    return DiamondEmeraldExchangeEvent(
        request_id=request_id,
        player_uuid=player_uuid,
        player_name=player_name,
        diamond_count=diamond_count,
        emerald_count=emerald_count,
        occurred_at=occurred_at,
    )


__all__ = [
    "DiamondEmeraldExchangeEvent",
    "DiamondEmeraldExchangeResult",
    "EmeraldDiamondExchangeEvent",
    "EmeraldDiamondExchangeResult",
    "diamond_emerald_exchange_command",
    "emerald_diamond_exchange_command",
    "parse_diamond_emerald_exchange_event",
    "parse_diamond_emerald_exchange_result",
    "parse_emerald_diamond_exchange_event",
    "parse_emerald_diamond_exchange_result",
]
