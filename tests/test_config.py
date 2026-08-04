from pathlib import Path

import pytest

from mc_bot.config import Config


def test_loads_required_and_default_configuration() -> None:
    config = Config.from_environment({"DISCORD_TOKEN": "secret"})
    assert config.discord_token == "secret"
    assert config.minecraft_log_path == Path("/minecraft/logs/latest.log")
    assert config.minecraft_whitelist_path == Path("/minecraft/whitelist.json")
    assert config.minecraft_server_properties_path == Path("/minecraft/server.properties")
    assert config.cursor_path == Path("/data/cursor.json")
    assert config.settings_path == Path("/data/settings.json")
    assert config.accounts_path == Path("/data/accounts.db")
    assert config.rcon_host == "minecraft"
    assert config.rcon_port == 25575
    assert config.rcon_password == ""
    assert config.floodgate_username_prefix == "."
    assert config.voicevox_tts_api_url == ""
    assert config.voicevox_tts_api_token == ""
    assert config.voicevox_speaker_id == 46
    assert config.voicevox_speed == 1.0
    assert config.level_bot_api_url == ""
    assert config.level_bot_api_token == ""
    assert config.minecraft_xp_poll_seconds == 30


def test_rejects_missing_token() -> None:
    with pytest.raises(ValueError, match="DISCORD_TOKEN is required"):
        Config.from_environment({})


def test_loads_rcon_configuration() -> None:
    config = Config.from_environment(
        {
            "DISCORD_TOKEN": "secret",
            "MINECRAFT_RCON_HOST": "mc",
            "MINECRAFT_RCON_PORT": "25576",
            "MINECRAFT_RCON_PASSWORD": "rcon-secret",
            "FLOODGATE_USERNAME_PREFIX": "*",
        }
    )

    assert config.rcon_host == "mc"
    assert config.rcon_port == 25576
    assert config.rcon_password == "rcon-secret"
    assert config.floodgate_username_prefix == "*"


def test_rejects_invalid_rcon_port() -> None:
    with pytest.raises(ValueError, match="MINECRAFT_RCON_PORT"):
        Config.from_environment({"DISCORD_TOKEN": "secret", "MINECRAFT_RCON_PORT": "invalid"})


def test_loads_voicevox_configuration() -> None:
    config = Config.from_environment(
        {
            "DISCORD_TOKEN": "secret",
            "VOICEVOX_TTS_API_URL": "https://tts.example.test/",
            "VOICEVOX_TTS_API_TOKEN": "tts-secret",
            "VOICEVOX_SPEAKER_ID": "3",
            "VOICEVOX_SPEED": "1.25",
        }
    )

    assert config.voicevox_tts_api_url == "https://tts.example.test"
    assert config.voicevox_tts_api_token == "tts-secret"
    assert config.voicevox_speaker_id == 3
    assert config.voicevox_speed == 1.25


def test_loads_level_bot_xp_configuration() -> None:
    config = Config.from_environment(
        {
            "DISCORD_TOKEN": "secret",
            "LEVEL_BOT_API_URL": "https://levels.example.test/",
            "LEVEL_BOT_API_TOKEN": "xp-secret",
            "MINECRAFT_XP_POLL_SECONDS": "60",
        }
    )

    assert config.level_bot_api_url == "https://levels.example.test"
    assert config.level_bot_api_token == "xp-secret"
    assert config.minecraft_xp_poll_seconds == 60


@pytest.mark.parametrize(
    "environment",
    [
        {"LEVEL_BOT_API_URL": "https://levels.example.test"},
        {"LEVEL_BOT_API_TOKEN": "xp-secret"},
        {
            "LEVEL_BOT_API_URL": "ftp://levels.example.test",
            "LEVEL_BOT_API_TOKEN": "xp-secret",
        },
        {"MINECRAFT_XP_POLL_SECONDS": "9"},
        {"MINECRAFT_XP_POLL_SECONDS": "61"},
        {"MINECRAFT_XP_POLL_SECONDS": "invalid"},
    ],
)
def test_rejects_invalid_level_bot_xp_configuration(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        Config.from_environment({"DISCORD_TOKEN": "secret", **environment})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VOICEVOX_TTS_API_URL", "ftp://tts.example.test"),
        ("VOICEVOX_SPEAKER_ID", "invalid"),
        ("VOICEVOX_SPEAKER_ID", "-1"),
        ("VOICEVOX_SPEED", "fast"),
        ("VOICEVOX_SPEED", "2.1"),
    ],
)
def test_rejects_invalid_voicevox_configuration(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        Config.from_environment({"DISCORD_TOKEN": "secret", name: value})
