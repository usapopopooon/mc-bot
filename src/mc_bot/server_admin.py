from __future__ import annotations

import json
import re

_FORMATTING_CODE = re.compile(r"§[0-9A-FK-OR]", re.IGNORECASE)
_PLAYER_NAME = re.compile(r"[A-Za-z0-9_.-]{1,32}")


def clean_rcon_output(response: str, *, limit: int = 1800) -> str:
    cleaned = _FORMATTING_CODE.sub("", response).replace("\r", "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def parse_online_players(response: str) -> list[str]:
    cleaned = clean_rcon_output(response)
    _, separator, names = cleaned.partition(":")
    if not separator or not names.strip():
        return []
    players = [name.strip() for name in names.split(",") if name.strip()]
    if any(not _PLAYER_NAME.fullmatch(name) for name in players):
        raise ValueError("Minecraftのオンラインプレイヤーを読み取れませんでした")
    return players


def kick_command(player_name: str, reason: str) -> str:
    if not _PLAYER_NAME.fullmatch(player_name):
        raise ValueError("無効なMinecraftプレイヤー名です")
    normalized_reason = " ".join(reason.split()).strip()
    if not 1 <= len(normalized_reason) <= 200:
        raise ValueError("キック理由は1から200文字で入力してください")
    return f"kick {player_name} {normalized_reason}"


def announcement_command(message: str) -> str:
    normalized = " ".join(message.split()).strip()
    if not 1 <= len(normalized) <= 200:
        raise ValueError("告知は1から200文字で入力してください")
    component = {"text": f"[サーバー告知] {normalized}", "color": "gold"}
    return "tellraw @a " + json.dumps(component, ensure_ascii=False, separators=(",", ":"))
