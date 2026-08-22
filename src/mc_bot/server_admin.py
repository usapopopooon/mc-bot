from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from mc_bot.player_names import is_safe_server_player_name

_FORMATTING_CODE = re.compile(r"§[0-9A-FK-OR]", re.IGNORECASE)
_PLAYER_LIST = re.compile(
    r"There are\s+(\d+)\s+of a max of\s+\d+\s+players online:(?:\s*(.*))?",
    re.IGNORECASE,
)
_RCON_ERROR_MARKERS = (
    "unknown command",
    "unknown item",
    "unknown or incomplete command",
    "incorrect argument",
    "no player was found",
    "no entity was found",
    "you do not have permission",
    "an unexpected error occurred",
)


@dataclass(frozen=True, slots=True)
class WhitelistedPlayer:
    name: str
    player_uuid: str


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
    if any(not is_safe_server_player_name(name) for name in players):
        raise ValueError("Minecraftのオンラインプレイヤーを読み取れませんでした")
    if declared_count != len(players):
        raise ValueError("Minecraftのオンライン人数とプレイヤー一覧が一致しません")
    return players


def read_whitelisted_profiles(whitelist_path: Path) -> list[WhitelistedPlayer]:
    try:
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"whitelist.jsonを読み取れませんでした: {error}") from error
    if not isinstance(data, list):
        raise ValueError("whitelist.jsonの形式が正しくありません")
    players: list[WhitelistedPlayer] = []
    for entry in data:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("uuid"), str)
        ):
            raise ValueError("whitelist.jsonの登録者を読み取れませんでした")
        name = entry["name"].strip()
        raw_uuid = entry["uuid"].strip()
        if not is_safe_server_player_name(name):
            raise ValueError("whitelist.jsonの登録者を読み取れませんでした")
        if not raw_uuid:
            raise ValueError(f"whitelist.jsonのUUIDが正しくありません: {name}")
        try:
            player_uuid = str(uuid.UUID(raw_uuid))
        except ValueError:
            # 既存の読み取り仕様ではUUIDの形式を検証していなかったため、古い・独自形式の
            # エントリも一覧表示できるよう保持する。新規追加時はupsert側で厳格に検証する。
            player_uuid = raw_uuid
        players.append(WhitelistedPlayer(name=name, player_uuid=player_uuid))
    return sorted(players, key=lambda player: player.name.casefold())


def read_whitelisted_players(whitelist_path: Path) -> list[str]:
    return [player.name for player in read_whitelisted_profiles(whitelist_path)]


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
    matched_profile: tuple[str, str] | None = None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        raw_uuid = entry.get("uuid")
        if not isinstance(name, str) or name.casefold() != normalized_name:
            continue
        try:
            profile = (name, str(uuid.UUID(raw_uuid)))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"usercache.jsonのUUIDが正しくありません: {name}") from error
        if matched_profile is not None and matched_profile[1] != profile[1]:
            raise ValueError(
                f"usercache.jsonで同じプレイヤー名が複数のUUIDに一致しています: {name}"
            )
        matched_profile = profile
    return matched_profile


def read_cached_player_profile_by_uuid(
    usercache_path: Path, player_uuid: str
) -> tuple[str, str] | None:
    try:
        normalized_uuid = str(uuid.UUID(player_uuid))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Minecraft UUIDが正しくありません") from error
    try:
        data = json.loads(usercache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"usercache.jsonを読み取れませんでした: {error}") from error
    if not isinstance(data, list):
        raise ValueError("usercache.jsonの形式が正しくありません")
    matched_profile: tuple[str, str] | None = None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        raw_uuid = entry.get("uuid")
        if not isinstance(name, str) or not isinstance(raw_uuid, str):
            continue
        try:
            entry_uuid = str(uuid.UUID(raw_uuid))
        except ValueError as error:
            raise ValueError(f"usercache.jsonのUUIDが正しくありません: {name}") from error
        if entry_uuid.casefold() != normalized_uuid.casefold():
            continue
        profile = (name, entry_uuid)
        if matched_profile is not None and matched_profile[0].casefold() != name.casefold():
            raise ValueError(
                f"usercache.jsonで同じUUIDが複数のプレイヤー名に一致しています: {entry_uuid}"
            )
        matched_profile = profile
    return matched_profile


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
    matching_uuid_index: int | None = None
    for index, entry in enumerate(data):
        entry_name = entry.get("name")
        entry_uuid = entry.get("uuid")
        same_name = isinstance(entry_name, str) and entry_name.casefold() == normalized_name
        same_uuid = (
            isinstance(entry_uuid, str) and entry_uuid.casefold() == normalized_uuid.casefold()
        )
        if same_name and not same_uuid:
            raise ValueError(
                f"whitelist.jsonを更新できませんでした: {player_name} は別のUUIDで登録されています"
            )
        if same_uuid:
            if matching_uuid_index is not None:
                raise ValueError(
                    "whitelist.jsonを更新できませんでした: "
                    f"{normalized_uuid} が複数登録されています"
                )
            matching_uuid_index = index
    if matching_uuid_index is None:
        data.append(replacement)
    else:
        data[matching_uuid_index] = replacement

    _write_whitelist_data(whitelist_path, data)


def remove_whitelisted_player(whitelist_path: Path, player_uuid: str) -> bool:
    try:
        normalized_uuid = str(uuid.UUID(player_uuid))
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"whitelist.jsonを更新できませんでした: {error}") from error
    if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
        raise ValueError("whitelist.jsonを更新できませんでした: 形式が正しくありません")
    retained = [
        entry
        for entry in data
        if not (
            isinstance(entry.get("uuid"), str)
            and entry["uuid"].casefold() == normalized_uuid.casefold()
        )
    ]
    if len(retained) == len(data):
        return False
    _write_whitelist_data(whitelist_path, retained)
    return True


def _write_whitelist_data(whitelist_path: Path, data: list[dict]) -> None:
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
    if not is_safe_server_player_name(player_name):
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
