from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    channel_id: int | None = None
    guild_id: int | None = None
    panel_channel_id: int | None = None
    panel_message_id: int | None = None
    admin_panel_channel_id: int | None = None
    admin_panel_message_id: int | None = None
    status_panel_channel_id: int | None = None
    status_panel_message_id: int | None = None
    approval_mode: str = "automatic"
    approval_channel_id: int | None = None
    player_count_channel_id: int | None = None
    player_count_enabled: bool = False
    voice_channel_id: int | None = None
    voice_enabled: bool = False
    whitelist_resume_at: float | None = None


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> RuntimeSettings:
        if not self._path.exists():
            return RuntimeSettings()

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read settings file {self._path}: {error}") from error
        if not isinstance(data, dict):
            raise ValueError("Settings file must contain a JSON object")

        identifiers = {}
        for name in (
            "channel_id",
            "guild_id",
            "panel_channel_id",
            "panel_message_id",
            "admin_panel_channel_id",
            "admin_panel_message_id",
            "status_panel_channel_id",
            "status_panel_message_id",
            "approval_channel_id",
            "player_count_channel_id",
            "voice_channel_id",
        ):
            value = data.get(name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer or null")
            identifiers[name] = value

        approval_mode = data.get("approval_mode", "automatic")
        if approval_mode not in {"automatic", "manual"}:
            raise ValueError("approval_mode must be automatic or manual")
        player_count_enabled = data.get("player_count_enabled", False)
        if not isinstance(player_count_enabled, bool):
            raise ValueError("player_count_enabled must be a boolean")
        voice_enabled = data.get("voice_enabled", False)
        if not isinstance(voice_enabled, bool):
            raise ValueError("voice_enabled must be a boolean")
        whitelist_resume_at = data.get("whitelist_resume_at")
        if whitelist_resume_at is not None and (
            not isinstance(whitelist_resume_at, int | float)
            or isinstance(whitelist_resume_at, bool)
            or not math.isfinite(whitelist_resume_at)
            or whitelist_resume_at <= 0
        ):
            raise ValueError("whitelist_resume_at must be a positive number or null")
        return RuntimeSettings(
            **identifiers,
            approval_mode=approval_mode,
            player_count_enabled=player_count_enabled,
            voice_enabled=voice_enabled,
            whitelist_resume_at=(
                float(whitelist_resume_at) if whitelist_resume_at is not None else None
            ),
        )

    def save(self, settings: RuntimeSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_name(f".{self._path.name}.tmp")
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n"
        temporary_path.write_text(payload, encoding="utf-8")
        os.replace(temporary_path, self._path)
