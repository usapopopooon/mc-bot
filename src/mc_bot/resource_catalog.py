from __future__ import annotations

import base64
import re
from collections.abc import Sequence
from typing import Protocol

RESOURCE_ITEM_ID_PATTERN = re.compile(r"^minecraft:[a-z0-9_]+$")
MAX_RESOURCE_PACKS = 25
MAX_RESOURCE_COST_XP = 10_000_000
MAX_RESOURCE_CATALOG_COMMAND_LENGTH = 8_000


class ResourcePackLike(Protocol):
    item_id: str
    item_name: str
    item_count: int
    cost_xp: int


def validate_resource_pack(pack: ResourcePackLike) -> None:
    if len(pack.item_id) > 64 or not RESOURCE_ITEM_ID_PATTERN.fullmatch(pack.item_id):
        raise ValueError("resource item_id is invalid")
    normalized_name = pack.item_name.strip()
    if (
        not normalized_name
        or len(normalized_name) > 64
        or any(ord(character) < 32 for character in normalized_name)
    ):
        raise ValueError("resource item_name is invalid")
    if not 1 <= pack.item_count <= 64 or not 1 <= pack.cost_xp <= MAX_RESOURCE_COST_XP:
        raise ValueError("resource pack amount or cost is invalid")


def is_valid_resource_item_id(item_id: str) -> bool:
    return len(item_id) <= 64 and RESOURCE_ITEM_ID_PATTERN.fullmatch(item_id) is not None


def resource_catalog_sync_command(revision: int, packs: Sequence[ResourcePackLike]) -> str:
    if revision < 0 or not packs or len(packs) > MAX_RESOURCE_PACKS:
        raise ValueError("resource catalog size or revision is invalid")
    payload = _resource_pack_payload(packs)
    command = f"usapo-event-bridge resource-catalog-sync {revision} {payload}"
    if len(command) > MAX_RESOURCE_CATALOG_COMMAND_LENGTH:
        raise ValueError("resource catalog command is too large")
    return command


def resource_pack_validation_command(pack: ResourcePackLike) -> str:
    payload = _resource_pack_payload((pack,))
    command = f"usapo-event-bridge resource-pack-validate {payload}"
    if len(command) > MAX_RESOURCE_CATALOG_COMMAND_LENGTH:
        raise ValueError("resource pack validation command is too large")
    return command


def _resource_pack_payload(packs: Sequence[ResourcePackLike]) -> str:
    if not packs or len(packs) > MAX_RESOURCE_PACKS:
        raise ValueError("resource catalog size is invalid")
    identities: set[tuple[str, int]] = set()
    item_names: dict[str, str] = {}
    lines: list[str] = []
    for pack in packs:
        validate_resource_pack(pack)
        identity = (pack.item_id, pack.item_count)
        if identity in identities:
            raise ValueError("resource catalog contains a duplicate pack")
        identities.add(identity)
        item_name = pack.item_name.strip()
        if item_names.setdefault(pack.item_id, item_name) != item_name:
            raise ValueError("resource catalog contains inconsistent item names")
        lines.append(
            "\t".join(
                (
                    pack.item_id,
                    item_name,
                    str(pack.item_count),
                    str(pack.cost_xp),
                )
            )
        )
    return base64.urlsafe_b64encode("\n".join(lines).encode()).decode().rstrip("=")


__all__ = [
    "MAX_RESOURCE_PACKS",
    "is_valid_resource_item_id",
    "resource_catalog_sync_command",
    "resource_pack_validation_command",
    "validate_resource_pack",
]
