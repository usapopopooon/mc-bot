from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    discord_token: str
    minecraft_log_path: Path = Path("/minecraft/logs/latest.log")
    cursor_path: Path = Path("/data/cursor.json")
    settings_path: Path = Path("/data/settings.json")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Config:
        values = os.environ if environment is None else environment
        token = _required(values, "DISCORD_TOKEN")
        return cls(discord_token=token)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value
