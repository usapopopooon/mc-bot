from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    discord_token: str
    minecraft_log_path: Path = Path("/minecraft/logs/latest.log")
    minecraft_whitelist_path: Path = Path("/minecraft/whitelist.json")
    cursor_path: Path = Path("/data/cursor.json")
    settings_path: Path = Path("/data/settings.json")
    accounts_path: Path = Path("/data/accounts.db")
    rcon_host: str = "minecraft"
    rcon_port: int = 25575
    rcon_password: str = ""
    floodgate_username_prefix: str = "."

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Config:
        values = os.environ if environment is None else environment
        token = _required(values, "DISCORD_TOKEN")
        rcon_port_text = values.get("MINECRAFT_RCON_PORT", "25575").strip()
        try:
            rcon_port = int(rcon_port_text)
        except ValueError as error:
            raise ValueError("MINECRAFT_RCON_PORT must be an integer") from error
        if not 1 <= rcon_port <= 65535:
            raise ValueError("MINECRAFT_RCON_PORT must be between 1 and 65535")
        return cls(
            discord_token=token,
            rcon_host=values.get("MINECRAFT_RCON_HOST", "minecraft").strip() or "minecraft",
            rcon_port=rcon_port,
            rcon_password=values.get("MINECRAFT_RCON_PASSWORD", "").strip(),
            floodgate_username_prefix=values.get("FLOODGATE_USERNAME_PREFIX", "."),
        )


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value
