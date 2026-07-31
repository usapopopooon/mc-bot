import asyncio
import json

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.server_admin import (
    announcement_command,
    clean_rcon_output,
    kick_command,
    parse_online_players,
)
from mc_bot.settings import RuntimeSettings, SettingsStore


class FakeRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        return "ok"


def test_parses_online_players_from_rcon_list() -> None:
    response = "There are 3 of a max of 20 players online: Steve, .Bedrock_User, Alex"

    assert parse_online_players(response) == ["Steve", ".Bedrock_User", "Alex"]


def test_parses_empty_online_player_list() -> None:
    assert parse_online_players("There are 0 of a max of 20 players online:") == []


def test_rejects_unexpected_online_player_name() -> None:
    with pytest.raises(ValueError, match="読み取れません"):
        parse_online_players("There are 1 of a max of 20 players online: bad name")


def test_builds_kick_command_with_normalized_reason() -> None:
    assert kick_command(".Bedrock_User", "荒らし\n行為") == "kick .Bedrock_User 荒らし 行為"


def test_rejects_invalid_kick_player_name() -> None:
    with pytest.raises(ValueError, match="無効"):
        kick_command("@a", "reason")


def test_builds_safe_tellraw_announcement() -> None:
    command = announcement_command('"テスト" @everyone')
    prefix = "tellraw @a "

    assert command.startswith(prefix)
    assert json.loads(command.removeprefix(prefix)) == {
        "text": '[サーバー告知] "テスト" @everyone',
        "color": "gold",
    }


def test_cleans_minecraft_formatting_and_limits_output() -> None:
    assert clean_rcon_output("§aHealthy\r\n", limit=6) == "Healt…"


def test_automatically_resumes_persisted_whitelist_pause(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            settings_path=settings_path,
            rcon_password="secret",
        )
    )
    bot._settings = RuntimeSettings(whitelist_resume_at=1)
    rcon = FakeRcon()
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._resume_whitelist_if_due())

    assert rcon.commands == ["whitelist on"]
    assert bot._settings.whitelist_resume_at is None
    assert SettingsStore(settings_path).load().whitelist_resume_at is None
