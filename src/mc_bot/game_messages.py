from __future__ import annotations

import json

from mc_bot.player_names import is_safe_server_player_name


def private_tellraw_command(player_name: str, message: str) -> str:
    if not is_safe_server_player_name(player_name):
        raise ValueError("player_name contains unsafe RCON characters")
    plain_message = message.replace("**", "").replace("`", "")
    plain_message = " ".join(plain_message.splitlines()).strip()
    if not plain_message:
        raise ValueError("message must not be empty")
    component = {"text": plain_message[:500], "color": "yellow"}
    return (
        f"tellraw {player_name} {json.dumps(component, ensure_ascii=False, separators=(',', ':'))}"
    )


__all__ = ["private_tellraw_command"]
