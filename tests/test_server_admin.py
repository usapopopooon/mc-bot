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
    read_whitelist_enabled,
    validate_rcon_response,
)
from mc_bot.settings import RuntimeSettings, SettingsStore


class FakeRcon:
    def __init__(self, properties_path=None, events=None) -> None:
        self.commands: list[str] = []
        self.properties_path = properties_path
        self.events = events

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if self.events is not None:
            self.events.append(f"rcon:{command}")
        if self.properties_path is not None and command in {"whitelist on", "whitelist off"}:
            enabled = command == "whitelist on"
            self.properties_path.write_text(
                f"white-list={'true' if enabled else 'false'}\n",
                encoding="utf-8",
            )
        return "Whitelist is now turned on" if command == "whitelist on" else "ok"


class RecordingSettingsStore(SettingsStore):
    def __init__(self, path, events) -> None:
        super().__init__(path)
        self.events = events

    def save(self, settings: RuntimeSettings) -> None:
        state = "pause" if settings.whitelist_resume_at is not None else "clear"
        self.events.append(f"save:{state}")
        super().save(settings)


def test_parses_online_players_from_rcon_list() -> None:
    response = "There are 3 of a max of 20 players online: Steve, .Bedrock_User, Alex"

    assert parse_online_players(response) == ["Steve", ".Bedrock_User", "Alex"]


def test_parses_empty_online_player_list() -> None:
    assert parse_online_players("There are 0 of a max of 20 players online:") == []


def test_rejects_unexpected_online_player_name() -> None:
    with pytest.raises(ValueError, match="読み取れません"):
        parse_online_players("There are 1 of a max of 20 players online: bad name")


def test_rejects_failed_or_inconsistent_online_player_response() -> None:
    with pytest.raises(ValueError, match="読み取れません"):
        parse_online_players("Unknown command")
    with pytest.raises(ValueError, match="一致しません"):
        parse_online_players("There are 2 of a max of 20 players online: Steve")


def test_validates_rcon_command_response() -> None:
    assert validate_rcon_response("§aSet the time to 1000") == "Set the time to 1000"
    with pytest.raises(ValueError, match="Unknown command"):
        validate_rcon_response("Unknown command. Type /help for help.")


def test_reads_actual_whitelist_state(tmp_path) -> None:
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("motd=Test\nwhite-list=true\n", encoding="utf-8")

    assert read_whitelist_enabled(properties_path) is True

    properties_path.write_text("white-list=false\n", encoding="utf-8")
    assert read_whitelist_enabled(properties_path) is False


def test_rejects_missing_whitelist_property(tmp_path) -> None:
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("motd=Test\n", encoding="utf-8")

    with pytest.raises(ValueError, match="white-list"):
        read_whitelist_enabled(properties_path)


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
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("white-list=false\n", encoding="utf-8")
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            settings_path=settings_path,
            rcon_password="secret",
            minecraft_server_properties_path=properties_path,
        )
    )
    bot._settings = RuntimeSettings(whitelist_resume_at=1)
    rcon = FakeRcon(properties_path)
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._resume_whitelist_if_due())

    assert rcon.commands == ["whitelist on"]
    assert read_whitelist_enabled(properties_path) is True
    assert bot._settings.whitelist_resume_at is None
    assert SettingsStore(settings_path).load().whitelist_resume_at is None


def test_persists_resume_deadline_before_disabling_whitelist(tmp_path) -> None:
    events: list[str] = []
    settings_path = tmp_path / "settings.json"
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("white-list=true\n", encoding="utf-8")
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            settings_path=settings_path,
            rcon_password="secret",
            minecraft_server_properties_path=properties_path,
        )
    )
    bot._settings_store = RecordingSettingsStore(settings_path, events)
    bot._rcon = FakeRcon(properties_path, events)  # type: ignore[assignment]

    asyncio.run(bot._pause_whitelist_for(15))

    assert events == ["save:pause", "rcon:whitelist off"]
    assert read_whitelist_enabled(properties_path) is False
    assert bot._settings.whitelist_resume_at is not None


def test_serializes_whitelist_pause_and_resume(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    bot = MinecraftDiscordBot(Config(discord_token="secret", settings_path=settings_path))
    active_operations = 0
    maximum_active_operations = 0

    async def fake_set_whitelist_enabled(enabled: bool) -> None:
        nonlocal active_operations, maximum_active_operations
        active_operations += 1
        maximum_active_operations = max(maximum_active_operations, active_operations)
        await asyncio.sleep(0)
        active_operations -= 1

    bot._set_whitelist_enabled = fake_set_whitelist_enabled  # type: ignore[method-assign]

    async def exercise() -> None:
        pause = asyncio.create_task(bot._pause_whitelist_for(15))
        await asyncio.sleep(0)
        resume = asyncio.create_task(bot._resume_whitelist_now())
        await asyncio.gather(pause, resume)

    asyncio.run(exercise())

    assert maximum_active_operations == 1
    assert bot._settings.whitelist_resume_at is None
