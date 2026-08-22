from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from mc_bot.player_names import is_safe_server_player_name

_PREFIX = "[UsapoEventBridge] USAPO_ACTIVITY|1|"


class ActivityKind(StrEnum):
    FISHING = "fishing"
    WOODCUTTING = "woodcutting"
    WOODCUTTING_RESET = "woodcutting_reset"
    EXPERIENCE = "experience"


@dataclass(frozen=True, slots=True)
class MinecraftActivityEvent:
    event_id: str
    kind: ActivityKind
    player_uuid: str
    player_name: str
    amount: int
    occurred_at: str


def parse_activity_event(line: str) -> MinecraftActivityEvent | None:
    _, separator, message = line.partition("]: ")
    if not separator or not message.startswith(_PREFIX):
        return None

    fields = message.removeprefix(_PREFIX).split("|")
    if len(fields) != 6:
        raise ValueError("Minecraft activity event has an invalid field count")
    (
        event_id_text,
        kind_text,
        player_uuid_text,
        encoded_name,
        amount_text,
        milliseconds_text,
    ) = fields
    try:
        event_id = str(uuid.UUID(event_id_text))
        player_uuid = str(uuid.UUID(player_uuid_text))
        kind = ActivityKind(kind_text)
        padding = "=" * (-len(encoded_name) % 4)
        player_name = base64.b64decode(
            encoded_name + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        amount = int(amount_text)
        milliseconds = int(milliseconds_text)
        occurred_at = datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        OverflowError,
        OSError,
    ) as error:
        raise ValueError("Minecraft activity event contains an invalid value") from error
    if (
        not is_safe_server_player_name(player_name)
        or amount <= 0
        or milliseconds < 0
        or (kind is not ActivityKind.EXPERIENCE and amount != 1)
    ):
        raise ValueError("Minecraft activity event contains an invalid player or timestamp")
    return MinecraftActivityEvent(
        event_id=event_id,
        kind=kind,
        player_uuid=player_uuid,
        player_name=player_name,
        amount=amount,
        occurred_at=occurred_at,
    )
