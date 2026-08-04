from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from contextlib import suppress
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
    "no entity was found",
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


def read_whitelisted_players(whitelist_path: Path) -> list[str]:
    try:
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"whitelist.jsonを読み取れませんでした: {error}") from error
    if not isinstance(data, list):
        raise ValueError("whitelist.jsonの形式が正しくありません")
    players: list[str] = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("whitelist.jsonの登録者を読み取れませんでした")
        name = entry["name"].strip()
        if not _PLAYER_NAME.fullmatch(name):
            raise ValueError("whitelist.jsonの登録者を読み取れませんでした")
        players.append(name)
    return sorted(players, key=str.casefold)


def read_cached_player_profile(usercache_path: Path, player_name: str) -> tuple[str, str] | None:
    try:
        data = json.loads(usercache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"usercache.jsonを読み取れませんでした: {error}") from error
    if not isinstance(data, list):
        raise ValueError("usercache.jsonの形式が正しくありません")
    normalized_name = player_name.casefold()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        raw_uuid = entry.get("uuid")
        if not isinstance(name, str) or name.casefold() != normalized_name:
            continue
        try:
            return name, str(uuid.UUID(raw_uuid))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"usercache.jsonのUUIDが正しくありません: {name}") from error
    return None


def upsert_whitelisted_player(whitelist_path: Path, player_name: str, player_uuid: str) -> None:
    try:
        normalized_uuid = str(uuid.UUID(player_uuid))
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"whitelist.jsonを更新できませんでした: {error}") from error
    if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
        raise ValueError("whitelist.jsonを更新できませんでした: 形式が正しくありません")

    normalized_name = player_name.casefold()
    replacement = {"uuid": normalized_uuid, "name": player_name}
    for index, entry in enumerate(data):
        entry_name = entry.get("name")
        entry_uuid = entry.get("uuid")
        same_name = isinstance(entry_name, str) and entry_name.casefold() == normalized_name
        same_uuid = (
            isinstance(entry_uuid, str) and entry_uuid.casefold() == normalized_uuid.casefold()
        )
        if same_name or same_uuid:
            data[index] = replacement
            break
    else:
        data.append(replacement)

    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=whitelist_path.parent,
            prefix=f".{whitelist_path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = temporary.name
        os.chmod(temporary_path, whitelist_path.stat().st_mode & 0o777)
        os.replace(temporary_path, whitelist_path)
    except OSError as error:
        if temporary_path is not None:
            with suppress(OSError):
                os.unlink(temporary_path)
        raise ValueError(f"whitelist.jsonを更新できませんでした: {error}") from error


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
