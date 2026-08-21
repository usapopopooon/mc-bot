from __future__ import annotations

import uuid
from dataclasses import dataclass

_RESULT_PREFIX = "USAPO_MATERIAL_BUYBACK_RESULT|1|"
_RELEASE_RESULT_PREFIX = "USAPO_MATERIAL_BUYBACK_RELEASE_RESULT|1|"
_ITEM_IDS = frozenset(
    {
        "minecraft:dirt",
        "minecraft:sand",
        "minecraft:sandstone",
        "minecraft:deepslate",
        "minecraft:cobbled_deepslate",
        "minecraft:tuff",
    }
)
_STATUSES = frozenset({"completed", "insufficient_items", "player_offline", "storage_error"})


@dataclass(frozen=True, slots=True)
class MaterialBuybackResult:
    request_id: str
    status: str
    item_id: str
    item_count: int
    duplicate: bool


@dataclass(frozen=True, slots=True)
class MaterialBuybackReleaseResult:
    request_id: str
    player_uuid: str
    status: str


def material_buyback_command(
    player_uuid: str,
    item_id: str,
    item_count: int,
    request_id: str,
) -> str:
    normalized_player_uuid = str(uuid.UUID(player_uuid))
    normalized_request_id = str(uuid.UUID(request_id))
    if item_id not in _ITEM_IDS or not _valid_count(item_count):
        raise ValueError("invalid material buyback selection")
    return (
        "usapo-event-bridge material-buyback "
        f"{normalized_player_uuid} {item_id} {item_count} {normalized_request_id}"
    )


def parse_material_buyback_result(
    response: str,
    *,
    expected_request_id: str,
    expected_item_id: str,
    expected_item_count: int,
) -> MaterialBuybackResult:
    text = response.strip()
    fields = text.removeprefix(_RESULT_PREFIX).split("|")
    if not text.startswith(_RESULT_PREFIX) or len(fields) != 5:
        raise ValueError("Minecraft returned an invalid material buyback result")
    request_text, status, item_id, count_text, disposition = fields
    try:
        request_id = str(uuid.UUID(request_text))
        expected_request = str(uuid.UUID(expected_request_id))
        item_count = int(count_text)
    except ValueError as error:
        raise ValueError("Minecraft returned an invalid material buyback result") from error
    if (
        request_id != expected_request
        or status not in _STATUSES
        or item_id not in _ITEM_IDS
        or item_id != expected_item_id
        or not _valid_count(item_count)
        or item_count != expected_item_count
        or disposition not in {"new", "duplicate"}
        or (status != "completed" and disposition == "duplicate")
    ):
        raise ValueError("Minecraft returned an invalid material buyback result")
    return MaterialBuybackResult(
        request_id=request_id,
        status=status,
        item_id=item_id,
        item_count=item_count,
        duplicate=disposition == "duplicate",
    )


def material_buyback_release_command(player_uuid: str, request_id: str) -> str:
    normalized_player_uuid = str(uuid.UUID(player_uuid))
    normalized_request_id = str(uuid.UUID(request_id))
    return (
        "usapo-event-bridge material-buyback-release "
        f"{normalized_player_uuid} {normalized_request_id}"
    )


def parse_material_buyback_release_result(
    response: str,
    *,
    expected_player_uuid: str,
    expected_request_id: str,
) -> MaterialBuybackReleaseResult:
    text = response.strip()
    fields = text.removeprefix(_RELEASE_RESULT_PREFIX).split("|")
    if not text.startswith(_RELEASE_RESULT_PREFIX) or len(fields) != 3:
        raise ValueError("Minecraft returned an invalid material buyback release result")
    request_text, player_text, status = fields
    try:
        request_id = str(uuid.UUID(request_text))
        player_uuid = str(uuid.UUID(player_text))
        expected_request = str(uuid.UUID(expected_request_id))
        expected_player = str(uuid.UUID(expected_player_uuid))
    except ValueError as error:
        raise ValueError("Minecraft returned an invalid material buyback release result") from error
    if (
        request_id != expected_request
        or player_uuid != expected_player
        or status not in {"released", "not_pending", "request_mismatch"}
    ):
        raise ValueError("Minecraft returned an invalid material buyback release result")
    return MaterialBuybackReleaseResult(request_id, player_uuid, status)


def _valid_count(item_count: int) -> bool:
    return 64 <= item_count <= 2_304 and item_count % 64 == 0


__all__ = [
    "MaterialBuybackReleaseResult",
    "MaterialBuybackResult",
    "material_buyback_command",
    "material_buyback_release_command",
    "parse_material_buyback_release_result",
    "parse_material_buyback_result",
]
