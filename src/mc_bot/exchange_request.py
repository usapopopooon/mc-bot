from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from mc_bot.player_names import is_safe_server_player_name

ExchangeRequestKind = Literal["balance", "xp", "resource", "emerald_diamond"]

_PREFIX = "[UsapoEventBridge] USAPO_EXCHANGE_REQUEST|1|"
_RESOURCE_TARGETS = {"minecraft:diamond", "minecraft:emerald"}


@dataclass(frozen=True, slots=True)
class MinecraftExchangeRequest:
    request_id: str
    player_uuid: str
    player_name: str
    kind: ExchangeRequestKind
    target: str
    amount: int
    expected_cost_xp: int
    expected_reward: int
    requested_at: str


def parse_exchange_request(line: str) -> MinecraftExchangeRequest | None:
    _, separator, message = line.partition("]: ")
    if not separator or not message.startswith(_PREFIX):
        return None
    fields = message.removeprefix(_PREFIX).split("|")
    if len(fields) != 9:
        raise ValueError("Minecraft exchange request has an invalid field count")
    (
        request_id_text,
        player_uuid_text,
        encoded_name,
        kind_text,
        target,
        amount_text,
        cost_text,
        reward_text,
        milliseconds_text,
    ) = fields
    try:
        request_id = str(uuid.UUID(request_id_text))
        player_uuid = str(uuid.UUID(player_uuid_text))
        padding = "=" * (-len(encoded_name) % 4)
        player_name = base64.b64decode(
            encoded_name + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        if kind_text not in {"balance", "xp", "resource", "emerald_diamond"}:
            raise ValueError("unknown exchange request kind")
        kind = cast(ExchangeRequestKind, kind_text)
        amount = int(amount_text)
        expected_cost_xp = int(cost_text)
        expected_reward = int(reward_text)
        milliseconds = int(milliseconds_text)
        requested_at = datetime.fromtimestamp(milliseconds / 1_000, UTC).isoformat()
    except (ValueError, UnicodeDecodeError, binascii.Error, OverflowError, OSError) as error:
        raise ValueError("Minecraft exchange request contains an invalid value") from error
    if not is_safe_server_player_name(player_name) or milliseconds < 0:
        raise ValueError("Minecraft exchange request contains an invalid player or timestamp")
    if not _valid_selection(kind, target, amount, expected_cost_xp, expected_reward):
        raise ValueError("Minecraft exchange request contains an invalid selection")
    return MinecraftExchangeRequest(
        request_id=request_id,
        player_uuid=player_uuid,
        player_name=player_name,
        kind=kind,
        target=target,
        amount=amount,
        expected_cost_xp=expected_cost_xp,
        expected_reward=expected_reward,
        requested_at=requested_at,
    )


def _valid_selection(
    kind: ExchangeRequestKind,
    target: str,
    amount: int,
    expected_cost_xp: int,
    expected_reward: int,
) -> bool:
    if kind == "balance":
        return target == "balance" and amount == expected_cost_xp == expected_reward == 0
    if kind == "xp":
        return (
            target == "minecraft:experience"
            and amount > 0
            and expected_cost_xp > 0
            and expected_reward == amount
        )
    if kind == "resource":
        return (
            target in _RESOURCE_TARGETS
            and 1 <= amount <= 64
            and expected_cost_xp > 0
            and expected_reward == amount
        )
    return (
        kind == "emerald_diamond"
        and target == "minecraft:diamond"
        and amount in {32, 64}
        and expected_cost_xp == 0
        and expected_reward == amount // 32
    )


__all__ = ["MinecraftExchangeRequest", "parse_exchange_request"]
