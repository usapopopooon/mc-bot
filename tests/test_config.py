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
