from __future__ import annotations

import json
import re
from pathlib import Path

_FORMATTING_CODE = re.compile(r"§[0-9A-FK-OR]", re.IGNORECASE)
_PLAYER_NAME = re.compile(r"[A-Za-z0-9_.-]{1,32}")
_PLAYER_LIST = re.compile(
    r"There are\s+(\d+)\s+of a max of\s+\d+\s+players online:(?:\s*(.*))?",
    re.IGNORECASE,
)
_RCON_ERROR_MARKERS = (
    "unknown command",
    "unknown or incomplete command",
    "incorrect argument",
    "no player was found",
    "you do not have permission",
    "an unexpected error occurred",
)


def clean_rcon_output(response: str, *, limit: int = 1800) -> str:
    cleaned = _FORMATTING_CODE.sub("", response).replace("\r", "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def parse_online_players(response: str) -> list[str]:
    cleaned = clean_rcon_output(response)
    match = _PLAYER_LIST.fullmatch(cleaned)
    if match is None:
        raise ValueError("Minecraftのオンラインプレイヤーを読み取れませんでした")
    declared_count = int(match[1])
    names = match[2] or ""
    players = [name.strip() for name in names.split(",") if name.strip()]
    if any(not _PLAYER_NAME.fullmatch(name) for name in players):
        raise ValueError("Minecraftのオンラインプレイヤーを読み取れませんでした")
    if declared_count != len(players):
        raise ValueError("Minecraftのオンライン人数とプレイヤー一覧が一致しません")
    return players


def validate_rcon_response(response: str) -> str:
    cleaned = clean_rcon_output(response)
    lowered = cleaned.casefold()
    if any(marker in lowered for marker in _RCON_ERROR_MARKERS):
        raise ValueError(cleaned or "Minecraftコマンドが失敗しました")
    return cleaned


def read_whitelist_enabled(properties_path: Path) -> bool:
    try:
        lines = properties_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"server.propertiesを読み取れませんでした: {error}") from error
    value: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, candidate = line.split("=", 1)
        if key.strip() == "white-list":
            value = candidate.strip().casefold()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("server.propertiesのwhite-list設定を読み取れませんでした")


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
