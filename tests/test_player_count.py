import asyncio

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.player_count import parse_online_player_count, player_count_channel_name
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
        (None, "🔴\N{FULLWIDTH VERTICAL LINE}マイクラ停止中"),
        (0, "⚪\N{FULLWIDTH VERTICAL LINE}マイクラ 0人"),
        (3, "🟢\N{FULLWIDTH VERTICAL LINE}マイクラ 3人"),
    ],
)
def test_formats_player_count_channel_name(count: int | None, expected: str) -> None:
    assert player_count_channel_name(count) == expected


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
        self.name = options["name"]


def test_refreshes_channel_from_rcon_only_when_name_changes() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(
        guild_id=1,
        player_count_channel_id=2,
        player_count_enabled=True,
    )
    rcon = FakeRcon("There are 2 of a max of 20 players online: Steve, Alex")
    bot._rcon = rcon  # type: ignore[assignment]
    channel = FakeVoiceChannel("⚪\N{FULLWIDTH VERTICAL LINE}マイクラ 0人")

    asyncio.run(bot._refresh_player_count_channel(channel))  # type: ignore[arg-type]
    asyncio.run(bot._refresh_player_count_channel(channel))  # type: ignore[arg-type]

    assert rcon.commands == ["list", "list"]
    assert [edit["name"] for edit in channel.edits] == ["🟢\N{FULLWIDTH VERTICAL LINE}マイクラ 2人"]


def test_marks_channel_stopped_when_rcon_is_unavailable() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(
        guild_id=1,
        player_count_channel_id=2,
        player_count_enabled=True,
    )
    bot._rcon = FakeRcon(OSError("offline"))  # type: ignore[assignment]
    channel = FakeVoiceChannel("🟢\N{FULLWIDTH VERTICAL LINE}マイクラ 2人")

    asyncio.run(bot._refresh_player_count_channel(channel))  # type: ignore[arg-type]

    assert channel.name == "🔴\N{FULLWIDTH VERTICAL LINE}マイクラ停止中"
