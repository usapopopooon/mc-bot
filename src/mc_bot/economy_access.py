from __future__ import annotations

import uuid

WORLD_RESTRICTED_MESSAGE = (
    "world_2 と専用ネザー・エンドでは、ガチャ・フリマ・交換・クエストは利用できません。"
    "world_1 側へ移動してからご利用ください。"
)
ECONOMY_UNAVAILABLE_MESSAGE = (
    "現在のワールドを確認できないため、操作できません。少し待ってから再度お試しください。"
)
_PREFIX = "USAPO_ECONOMY_ACCESS_RESULT|1|"
_RESTRICTED_DIMENSIONS = (
    "minecraft:resource",
    "minecraft:world_2_nether",
    "minecraft:world_2_the_end",
)


def economy_access_command(player_uuid: str, request_id: str) -> str:
    return f"usapo-event-bridge economy-access {uuid.UUID(player_uuid)} {uuid.UUID(request_id)}"


def parse_economy_access_result(response: str, *, request_id: str) -> str:
    fields = response.strip().removeprefix(_PREFIX).split("|")
    if not response.strip().startswith(_PREFIX) or len(fields) != 2:
        raise ValueError("Minecraft returned an invalid economy access result")
    returned_request, status = fields
    if uuid.UUID(returned_request) != uuid.UUID(request_id) or status not in {
        "allowed",
        "world_restricted",
        "player_offline",
    }:
        raise ValueError("Minecraft returned an invalid economy access result")
    return status


def guarded_economy_delivery_command(player_uuid: str, command: str) -> str:
    """Recheck on the server tick that applies the reward, targeting the same UUID."""
    if command.startswith("give "):
        _, _, arguments = command.split(" ", 2)
        reward_command = f"give @s {arguments}"
    elif command.startswith("experience add "):
        _, _, _, arguments = command.split(" ", 3)
        reward_command = f"experience add @s {arguments}"
    else:
        raise ValueError("only give and experience add may use the economy delivery guard")
    conditions = " ".join(f"unless dimension {dimension}" for dimension in _RESTRICTED_DIMENSIONS)
    return f"execute as {uuid.UUID(player_uuid)} at @s {conditions} run {reward_command}"
