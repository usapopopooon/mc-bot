from pathlib import Path

import pytest

from mc_bot.config import Config


def test_loads_required_and_default_configuration() -> None:
    config = Config.from_environment({"DISCORD_TOKEN": "secret"})
    assert config.discord_token == "secret"
    assert config.minecraft_log_path == Path("/minecraft/logs/latest.log")
    assert config.cursor_path == Path("/data/cursor.json")
    assert config.settings_path == Path("/data/settings.json")


def test_rejects_missing_token() -> None:
    with pytest.raises(ValueError, match="DISCORD_TOKEN is required"):
        Config.from_environment({})
