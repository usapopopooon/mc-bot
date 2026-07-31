from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    discord_token: str
    minecraft_log_path: Path = Path("/minecraft/logs/latest.log")
    minecraft_whitelist_path: Path = Path("/minecraft/whitelist.json")
    minecraft_server_properties_path: Path = Path("/minecraft/server.properties")
    cursor_path: Path = Path("/data/cursor.json")
    settings_path: Path = Path("/data/settings.json")
    accounts_path: Path = Path("/data/accounts.db")
    rcon_host: str = "minecraft"
    rcon_port: int = 25575
    rcon_password: str = ""
    floodgate_username_prefix: str = "."
    voicevox_tts_api_url: str = ""
    voicevox_tts_api_token: str = ""
    voicevox_speaker_id: int = 46
    voicevox_speed: float = 1.0

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
        tts_api_url = values.get("VOICEVOX_TTS_API_URL", "").strip().rstrip("/")
        if tts_api_url and not tts_api_url.startswith(("http://", "https://")):
            raise ValueError("VOICEVOX_TTS_API_URL must be an HTTP or HTTPS URL")
        speaker_text = values.get("VOICEVOX_SPEAKER_ID", "46").strip()
        try:
            speaker_id = int(speaker_text)
        except ValueError as error:
            raise ValueError("VOICEVOX_SPEAKER_ID must be an integer") from error
        if not 0 <= speaker_id <= 99999:
            raise ValueError("VOICEVOX_SPEAKER_ID must be between 0 and 99999")
        speed_text = values.get("VOICEVOX_SPEED", "1.0").strip()
        try:
            speed = float(speed_text)
        except ValueError as error:
            raise ValueError("VOICEVOX_SPEED must be a number") from error
        if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
            raise ValueError("VOICEVOX_SPEED must be between 0.5 and 2.0")
        return cls(
            discord_token=token,
            rcon_host=values.get("MINECRAFT_RCON_HOST", "minecraft").strip() or "minecraft",
            rcon_port=rcon_port,
            rcon_password=values.get("MINECRAFT_RCON_PASSWORD", "").strip(),
            floodgate_username_prefix=values.get("FLOODGATE_USERNAME_PREFIX", "."),
            voicevox_tts_api_url=tts_api_url,
            voicevox_tts_api_token=values.get("VOICEVOX_TTS_API_TOKEN", "").strip(),
            voicevox_speaker_id=speaker_id,
            voicevox_speed=speed,
        )


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value
