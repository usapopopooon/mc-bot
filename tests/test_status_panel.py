import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.settings import RuntimeSettings
from mc_bot.status_panel import (
    ServerStatusSnapshot,
    StatusPlayer,
    parse_server_list_response,
    status_panel_embed,
)


def test_parses_online_players_and_capacity() -> None:
    response = "There are 3 of a max of 20 players online: Steve, .Bedrock_User, Alex"

    assert parse_server_list_response(response) == (
        ["Steve", ".Bedrock_User", "Alex"],
        20,
    )


def test_rejects_inconsistent_player_list() -> None:
    with pytest.raises(ValueError, match="一致しません"):
        parse_server_list_response("There are 2 of a max of 20 players online: Steve")


def test_online_embed_includes_clickable_identity_without_plain_ping() -> None:
    snapshot = ServerStatusSnapshot(
        online=True,
        players=(
            StatusPlayer("Steve", 123456789),
            StatusPlayer(".Bedrock_User", None),
        ),
        max_players=20,
        checked_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    embed = status_panel_embed(snapshot)

    assert embed.description == "🟢 **オンライン**"
    assert embed.fields[0].value == "**2 / 20人**"
    assert "**Steve** (<@123456789>)" in embed.fields[1].value
    assert r"**.Bedrock\_User** (Discord未連携)" in embed.fields[1].value
    assert embed.footer.text == "最終更新"


def test_online_embed_handles_empty_server() -> None:
    snapshot = ServerStatusSnapshot(
        online=True,
        players=(),
        max_players=20,
        checked_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    embed = status_panel_embed(snapshot)

    assert embed.fields[0].value == "**0 / 20人**"
    assert embed.fields[1].value == "現在オンラインのプレイヤーはいません。"


def test_offline_embed_keeps_permanent_panel_visible() -> None:
    snapshot = ServerStatusSnapshot(
        online=False,
        players=(),
        max_players=None,
        checked_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    embed = status_panel_embed(snapshot)

    assert embed.description == "🔴 **オフライン**"
    assert embed.fields[0].value == "—"
    assert embed.footer.text == "最終確認"


def test_large_player_list_stays_within_discord_embed_limit() -> None:
    snapshot = ServerStatusSnapshot(
        online=True,
        players=tuple(
            StatusPlayer(f"Player_{index:03d}_long_name", index + 1) for index in range(100)
        ),
        max_players=100,
        checked_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    embed = status_panel_embed(snapshot)

    assert len(embed) <= 6_000
    assert any("ほか 50人" in field.value for field in embed.fields)


class FakeStatusMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edits: list[dict[str, object]] = []

    async def edit(self, **options: object) -> None:
        self.edits.append(options)


class FakeStatusChannel:
    def __init__(self) -> None:
        self.message: FakeStatusMessage | None = None
        self.sent: list[dict[str, object]] = []

    async def send(self, **options: object) -> FakeStatusMessage:
        self.sent.append(options)
        self.message = FakeStatusMessage(987)
        return self.message

    async def fetch_message(self, message_id: int) -> FakeStatusMessage:
        assert self.message is not None
        assert message_id == self.message.id
        return self.message


def test_refresh_creates_then_reuses_same_status_message(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            settings_path=tmp_path / "settings.json",
        )
    )
    bot._settings = RuntimeSettings(guild_id=1, status_panel_channel_id=2)
    channel = FakeStatusChannel()
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )
    snapshot = ServerStatusSnapshot(
        online=True,
        players=(),
        max_players=20,
        checked_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    bot._read_server_status_snapshot = AsyncMock(  # type: ignore[method-assign]
        return_value=snapshot
    )

    asyncio.run(bot._refresh_status_panel())
    asyncio.run(bot._refresh_status_panel())

    assert bot._settings.status_panel_message_id == 987
    assert len(channel.sent) == 1
    assert channel.message is not None
    assert len(channel.message.edits) == 1


def test_periodic_refresh_runs_every_five_minutes(tmp_path, monkeypatch) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            settings_path=tmp_path / "settings.json",
        )
    )
    bot._next_status_panel_refresh_at = 300
    schedule = Mock()
    bot._schedule_status_panel_refresh = schedule  # type: ignore[method-assign]

    monkeypatch.setattr("mc_bot.bot.time.monotonic", lambda: 299)
    bot._schedule_periodic_status_panel_refresh()
    schedule.assert_not_called()

    monkeypatch.setattr("mc_bot.bot.time.monotonic", lambda: 300)
    bot._schedule_periodic_status_panel_refresh()
    schedule.assert_called_once_with(delay=0)
    assert bot._next_status_panel_refresh_at == 600
