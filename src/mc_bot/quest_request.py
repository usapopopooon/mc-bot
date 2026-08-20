from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from mc_bot.player_names import is_safe_server_player_name

_PREFIX = "[UsapoEventBridge] USAPO_QUEST_STATE|1|"

type QuestTransitionKind = Literal[
    "created",
    "accepted",
    "abandoned",
    "reopened",
    "completed",
    "cancelled",
    "expired",
    "invalidated",
    "snapshot",
]
type QuestStatus = Literal["open", "accepted", "completed", "cancelled"]


@dataclass(frozen=True, slots=True)
class MinecraftQuestStateEvent:
    transition_id: str
    transition_kind: QuestTransitionKind
    quest_id: int
    event_id: str
    owner_uuid: str
    owner_name: str
    worker_uuid: str | None
    worker_name: str | None
    requested_item_id: str
    requested_item_name: str
    requested_count: int
    reward_item_id: str
    reward_item_name: str
    reward_count: int
    fulfillment_hours: int
    status: QuestStatus
    open_expires_at: str
    accepted_deadline: str | None
    created_at: str
    published_at: str


def parse_quest_state(line: str) -> MinecraftQuestStateEvent | None:
    message = _message(line)
    if message is None or not message.startswith(_PREFIX):
        return None
    fields = message.removeprefix(_PREFIX).split("|")
    if len(fields) != 20:
        raise ValueError("Minecraft quest state has an invalid field count")
    try:
        transition_id = str(uuid.UUID(fields[0]))
        if fields[1] not in {
            "created",
            "accepted",
            "abandoned",
            "reopened",
            "completed",
            "cancelled",
            "expired",
            "invalidated",
            "snapshot",
        }:
            raise ValueError("unknown quest transition kind")
        transition_kind = cast("QuestTransitionKind", fields[1])
        quest_id = int(fields[2])
        event_id = str(uuid.UUID(fields[3]))
        owner_uuid = str(uuid.UUID(fields[4]))
        owner_name = _decode(fields[5])
        worker_uuid = None if fields[6] == "-" else str(uuid.UUID(fields[6]))
        worker_name = None if fields[7] == "-" else _decode(fields[7])
        requested_item_id = _decode(fields[8])
        requested_item_name = _decode(fields[9])
        requested_count = int(fields[10])
        reward_item_id = _decode(fields[11])
        reward_item_name = _decode(fields[12])
        reward_count = int(fields[13])
        fulfillment_hours = int(fields[14])
        if fields[15] not in {"open", "accepted", "completed", "cancelled"}:
            raise ValueError("unknown quest status")
        status = cast("QuestStatus", fields[15])
        open_expires_millis = int(fields[16])
        accepted_deadline_millis = int(fields[17])
        created_millis = int(fields[18])
        published_millis = int(fields[19])
        open_expires_at = _timestamp(open_expires_millis)
        accepted_deadline = (
            None if accepted_deadline_millis == 0 else _timestamp(accepted_deadline_millis)
        )
        created_at = _timestamp(created_millis)
        published_at = _timestamp(published_millis)
    except (ValueError, UnicodeDecodeError, binascii.Error, OverflowError, OSError) as error:
        raise ValueError("Minecraft quest state contains an invalid value") from error
    has_worker = worker_uuid is not None and worker_name is not None
    if (
        quest_id <= 0
        or not is_safe_server_player_name(owner_name)
        or (worker_name is not None and not is_safe_server_player_name(worker_name))
        or has_worker != (status in {"accepted", "completed"})
        or (worker_uuid is None) != (worker_name is None)
        or not requested_item_id.startswith("minecraft:")
        or not reward_item_id.startswith("minecraft:")
        or not requested_item_name.strip()
        or not reward_item_name.strip()
        or not 1 <= requested_count <= 99
        or not 1 <= reward_count <= 99
        or not 1 <= fulfillment_hours <= 72
        or open_expires_millis <= created_millis
        or (status in {"accepted", "completed"}) != (accepted_deadline_millis > 0)
        or min(created_millis, published_millis) < 0
    ):
        raise ValueError("Minecraft quest state contains invalid quest data")
    return MinecraftQuestStateEvent(
        transition_id=transition_id,
        transition_kind=transition_kind,
        quest_id=quest_id,
        event_id=event_id,
        owner_uuid=owner_uuid,
        owner_name=owner_name,
        worker_uuid=worker_uuid,
        worker_name=worker_name,
        requested_item_id=requested_item_id,
        requested_item_name=requested_item_name[:120],
        requested_count=requested_count,
        reward_item_id=reward_item_id,
        reward_item_name=reward_item_name[:120],
        reward_count=reward_count,
        fulfillment_hours=fulfillment_hours,
        status=status,
        open_expires_at=open_expires_at,
        accepted_deadline=accepted_deadline,
        created_at=created_at,
        published_at=published_at,
    )


def _message(line: str) -> str | None:
    _, separator, message = line.partition("]: ")
    return message if separator else None


def _decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True).decode("utf-8")


def _timestamp(milliseconds: int) -> str:
    if milliseconds < 0:
        raise ValueError("negative timestamp")
    return datetime.fromtimestamp(milliseconds / 1_000, UTC).isoformat()


__all__ = ["MinecraftQuestStateEvent", "parse_quest_state"]
