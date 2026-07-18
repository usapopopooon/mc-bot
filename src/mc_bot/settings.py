from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    channel_id: int | None = None
    server_label: str = "Chill Cafe"


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

        channel_id = data.get("channel_id")
        server_label = data.get("server_label", "Chill Cafe")
        if channel_id is not None and (
            not isinstance(channel_id, int) or isinstance(channel_id, bool) or channel_id <= 0
        ):
            raise ValueError("channel_id must be a positive integer or null")
        if not isinstance(server_label, str) or not server_label.strip():
            raise ValueError("server_label must be a non-empty string")
        if len(server_label) > 64:
            raise ValueError("server_label must be at most 64 characters")
        return RuntimeSettings(channel_id=channel_id, server_label=server_label.strip())

    def save(self, settings: RuntimeSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_name(f".{self._path.name}.tmp")
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n"
        temporary_path.write_text(payload, encoding="utf-8")
        os.replace(temporary_path, self._path)
