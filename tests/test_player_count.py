import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.player_count import parse_online_player_count, player_count_status
from mc_bot.settings import RuntimeSettings


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("There are 0 of a max of 20 players online:", 0),
        ("There are 2 of a max of 20 players online: Steve, Alex", 2),
        ("There are 15 of a max of 100 players online: many players", 15),
    ],
)
def test_parses_online_player_count(response: str, expected: int) -> None:
    assert parse_online_player_count(response) == expected


def test_rejects_unexpected_rcon_response() -> None:
    with pytest.raises(ValueError, match="読み取れませんでした"):
        parse_online_player_count("Unknown command")


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (None, "🔴サーバー停止中"),
        (0, "⚪オンライン0人"),
        (3, "🟢オンライン3人"),
    ],
)
def test_formats_player_count_status(count: int | None, expected: str) -> None:
    assert player_count_status(count) == expected


class FakeRcon:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeVoiceChannel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.edits: list[dict[str, str]] = []

    async def edit(self, **options: str) -> None:
        self.edits.append(options)
        if "name" in options:
            self.name = options["name"]


def _voice_state(channel_id: int | None) -> SimpleNamespace:
    channel = None if channel_id is None else SimpleNamespace(id=channel_id)
    return SimpleNamespace(channel=channel)


def test_refreshes_status_from_rcon_only_when_count_changes() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(
        guild_id=1,
        player_count_channel_id=2,
        player_count_enabled=True,
    )
    rcon = FakeRcon("There are 2 of a max of 20 players online: Steve, Alex")
    bot._rcon = rcon  # type: ignore[assignment]
    channel = FakeVoiceChannel("マイクラオンライン数")

    asyncio.run(bot._refresh_player_count_channel(channel))  # type: ignore[arg-type]
    asyncio.run(bot._refresh_player_count_channel(channel))  # type: ignore[arg-type]

    assert rcon.commands == ["list", "list"]
    assert [edit["status"] for edit in channel.edits] == ["🟢オンライン2人"]


def test_marks_channel_stopped_when_rcon_is_unavailable() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(
        guild_id=1,
        player_count_channel_id=2,
        player_count_enabled=True,
    )
    bot._rcon = FakeRcon(OSError("offline"))  # type: ignore[assignment]
    channel = FakeVoiceChannel("マイクラオンライン数")

    asyncio.run(bot._refresh_player_count_channel(channel))  # type: ignore[arg-type]

    assert [edit["status"] for edit in channel.edits] == ["🔴サーバー停止中"]


@pytest.mark.parametrize(
    ("before_channel_id", "after_channel_id"),
    [
        (None, 2),
        (2, None),
        (3, 2),
        (2, 3),
    ],
)
def test_player_count_voice_activity_restores_same_status(
    before_channel_id: int | None,
    after_channel_id: int | None,
) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(
        guild_id=1,
        player_count_channel_id=2,
        player_count_enabled=True,
    )
    bot._last_player_count_status = "🟢オンライン2人"
    bot._schedule_player_count_refresh = Mock()  # type: ignore[method-assign]
    bot._sync_voice_bonus_for_discord_user = AsyncMock()  # type: ignore[method-assign]
    member = SimpleNamespace(id=100, bot=False, guild=SimpleNamespace(id=1))

    asyncio.run(
        bot.on_voice_state_update(
            member,  # type: ignore[arg-type]
            _voice_state(before_channel_id),  # type: ignore[arg-type]
            _voice_state(after_channel_id),  # type: ignore[arg-type]
        )
    )

    assert bot._last_player_count_status is None
    bot._schedule_player_count_refresh.assert_called_once_with(delay=1)  # type: ignore[attr-defined]

    bot._rcon = FakeRcon(  # type: ignore[assignment]
        "There are 2 of a max of 20 players online: Steve, Alex"
    )
    channel = FakeVoiceChannel("マイクラオンライン数")
    asyncio.run(bot._refresh_player_count_channel(channel))  # type: ignore[arg-type]

    assert [edit["status"] for edit in channel.edits] == ["🟢オンライン2人"]


def test_unrelated_voice_activity_keeps_player_count_status_cache() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(
        guild_id=1,
        player_count_channel_id=2,
        player_count_enabled=True,
    )
    bot._last_player_count_status = "🟢オンライン2人"
    bot._schedule_player_count_refresh = Mock()  # type: ignore[method-assign]
    bot._sync_voice_bonus_for_discord_user = AsyncMock()  # type: ignore[method-assign]
    member = SimpleNamespace(id=100, bot=False, guild=SimpleNamespace(id=1))

    asyncio.run(
        bot.on_voice_state_update(
            member,  # type: ignore[arg-type]
            _voice_state(3),  # type: ignore[arg-type]
            _voice_state(4),  # type: ignore[arg-type]
        )
    )

    assert bot._last_player_count_status == "🟢オンライン2人"
    bot._schedule_player_count_refresh.assert_not_called()  # type: ignore[attr-defined]
