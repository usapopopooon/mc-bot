"""Extend the existing survival-world RCON fakes without hiding guard regressions."""

from uuid import UUID


def allowed_economy_response(command: str) -> str | None:
    if not command.startswith("usapo-event-bridge economy-access "):
        return None
    _, _, player_uuid, request_id = command.split()
    UUID(player_uuid)
    UUID(request_id)
    return f"USAPO_ECONOMY_ACCESS_RESULT|1|{request_id}|allowed"


def unwrap_guarded_delivery(command: str, *, player_name: str = "Steve") -> str:
    if not command.startswith("execute as "):
        return command
    fields = command.split(" ", 3)
    UUID(fields[2])
    expected = (
        "at @s unless dimension minecraft:resource "
        "unless dimension minecraft:world_2_nether "
        "unless dimension minecraft:world_2_the_end run "
    )
    assert fields[3].startswith(expected)
    inner = fields[3].removeprefix(expected)
    assert inner.startswith(("give @s ", "experience add @s "))
    return inner.replace("@s", player_name, 1)
