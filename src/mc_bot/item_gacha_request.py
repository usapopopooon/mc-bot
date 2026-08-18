from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from mc_bot.item_gacha import ItemGachaCategory, ItemGachaKind
from mc_bot.player_names import is_safe_server_player_name

_PREFIX_V1 = "[UsapoEventBridge] USAPO_ITEM_GACHA_REQUEST|1|"
_PREFIX_V2 = "[UsapoEventBridge] USAPO_ITEM_GACHA_REQUEST|2|"
_PREFIX_V3 = "[UsapoEventBridge] USAPO_ITEM_GACHA_REQUEST|3|"


@dataclass(frozen=True, slots=True)
class MinecraftItemGachaRequest:
    request_id: str
    player_uuid: str
    player_name: str
    draw_category: ItemGachaCategory
    draw_kind: ItemGachaKind
    expected_cost_xp: int | None
    requested_at: str


def parse_item_gacha_request(line: str) -> MinecraftItemGachaRequest | None:
    _, separator, message = line.partition("]: ")
    if not separator:
        return None
    if message.startswith(_PREFIX_V3):
        fields = message.removeprefix(_PREFIX_V3).split("|")
        if len(fields) != 7:
            raise ValueError("Minecraft item gacha request has an invalid field count")
        (
            request_id_text,
            player_uuid_text,
            encoded_name,
            draw_category_text,
            draw_kind_text,
            expected_cost_text,
            milliseconds_text,
        ) = fields
    elif message.startswith(_PREFIX_V2):
        fields = message.removeprefix(_PREFIX_V2).split("|")
        if len(fields) != 6:
            raise ValueError("Minecraft item gacha request has an invalid field count")
        (
            request_id_text,
            player_uuid_text,
            encoded_name,
            draw_kind_text,
            expected_cost_text,
            milliseconds_text,
        ) = fields
        draw_category_text = "all"
    elif message.startswith(_PREFIX_V1):
        fields = message.removeprefix(_PREFIX_V1).split("|")
        if len(fields) != 5:
            raise ValueError("Minecraft item gacha request has an invalid field count")
        request_id_text, player_uuid_text, encoded_name, draw_kind_text, milliseconds_text = fields
        draw_category_text = "all"
        expected_cost_text = None
    else:
        return None
    try:
        request_id = str(uuid.UUID(request_id_text))
        player_uuid = str(uuid.UUID(player_uuid_text))
        padding = "=" * (-len(encoded_name) % 4)
        player_name = base64.b64decode(
            encoded_name + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        if draw_kind_text not in {"normal", "premium"}:
            raise ValueError("unknown item gacha kind")
        if draw_category_text not in {"all", "resources", "adventure", "equipment"}:
            raise ValueError("unknown item gacha category")
        draw_kind = cast(ItemGachaKind, draw_kind_text)
        draw_category = cast(ItemGachaCategory, draw_category_text)
        expected_cost_xp = int(expected_cost_text) if expected_cost_text is not None else None
        milliseconds = int(milliseconds_text)
        requested_at = datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        OverflowError,
        OSError,
    ) as error:
        raise ValueError("Minecraft item gacha request contains an invalid value") from error
    if (
        not is_safe_server_player_name(player_name)
        or milliseconds < 0
        or (expected_cost_xp is not None and expected_cost_xp <= 0)
    ):
        raise ValueError(
            "Minecraft item gacha request contains an invalid player, cost, or timestamp"
        )
    return MinecraftItemGachaRequest(
        request_id=request_id,
        player_uuid=player_uuid,
        player_name=player_name,
        draw_category=draw_category,
        draw_kind=draw_kind,
        expected_cost_xp=expected_cost_xp,
        requested_at=requested_at,
    )


__all__ = ["MinecraftItemGachaRequest", "parse_item_gacha_request"]
