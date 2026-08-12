import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.emerald_exchange import (
    emerald_diamond_exchange_command,
    parse_emerald_diamond_exchange_event,
    parse_emerald_diamond_exchange_result,
)
from mc_bot.settings import RuntimeSettings
from mc_bot.tailer import Cursor, PendingLine

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_UUID = "22222222-2222-4222-8222-222222222222"


def test_command_and_result_preserve_uuid_count_and_request_mapping() -> None:
    command = emerald_diamond_exchange_command(PLAYER_UUID, 32, REQUEST_ID)
    result = parse_emerald_diamond_exchange_result(
        f"USAPO_EMERALD_EXCHANGE_RESULT|2|{REQUEST_ID}|completed|32|1|new",
        expected_request_id=REQUEST_ID,
        expected_emerald_count=32,
    )

    assert command == (f"usapo-event-bridge emerald-diamond-v2 {PLAYER_UUID} 32 {REQUEST_ID}")
    assert result.status == "completed"
    assert result.emerald_count == 32
    assert result.diamond_count == 1
    assert not result.duplicate

    with pytest.raises(ValueError, match="32 or 64"):
        emerald_diamond_exchange_command(PLAYER_UUID, 16, REQUEST_ID)


def test_result_rejects_wrong_request_rate_and_duplicate_failure() -> None:
    with pytest.raises(ValueError):
        parse_emerald_diamond_exchange_result(
            "USAPO_EMERALD_EXCHANGE_RESULT|2|"
            "33333333-3333-4333-8333-333333333333|completed|32|1|new",
            expected_request_id=REQUEST_ID,
            expected_emerald_count=32,
        )
    with pytest.raises(ValueError):
        parse_emerald_diamond_exchange_result(
            f"USAPO_EMERALD_EXCHANGE_RESULT|2|{REQUEST_ID}|completed|32|2|new",
            expected_request_id=REQUEST_ID,
            expected_emerald_count=32,
        )
    with pytest.raises(ValueError):
        parse_emerald_diamond_exchange_result(
            f"USAPO_EMERALD_EXCHANGE_RESULT|2|{REQUEST_ID}|completed|64|1|new",
            expected_request_id=REQUEST_ID,
            expected_emerald_count=32,
        )
    with pytest.raises(ValueError):
        parse_emerald_diamond_exchange_result(
            f"USAPO_EMERALD_EXCHANGE_RESULT|2|{REQUEST_ID}|insufficient_emeralds|32|1|duplicate",
            expected_request_id=REQUEST_ID,
            expected_emerald_count=32,
        )


def test_parses_exchange_audit_event() -> None:
    event = parse_emerald_diamond_exchange_event(
        "[12:34:56] [Server thread/INFO]: [UsapoEventBridge] "
        f"USAPO_EMERALD_EXCHANGE|2|{REQUEST_ID}|{PLAYER_UUID}"
        "|Lll1a2kxOTkx|64|2|1786406400000"
    )

    assert event is not None
    assert event.request_id == REQUEST_ID
    assert event.player_uuid == PLAYER_UUID
    assert event.player_name == ".Yuki1991"
    assert event.emerald_count == 64
    assert event.diamond_count == 2
    assert event.occurred_at == "2026-08-11T00:00:00+00:00"


def test_parses_legacy_exchange_audit_event_for_delivery_retries() -> None:
    event = parse_emerald_diamond_exchange_event(
        "[12:34:56] [Server thread/INFO]: [UsapoEventBridge] "
        f"USAPO_EMERALD_EXCHANGE|1|{REQUEST_ID}|{PLAYER_UUID}"
        "|Lll1a2kxOTkx|16|1|1786406400000"
    )

    assert event is not None
    assert event.emerald_count == 16
    assert event.diamond_count == 1


def test_bot_executes_atomic_plugin_command_for_linked_uuid(tmp_path) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="test", accounts_path=tmp_path / "db"))
    account = SimpleNamespace(player_uuid=PLAYER_UUID)
    bot._online_exchange_account = AsyncMock(  # type: ignore[method-assign]
        return_value=(account, None)
    )
    bot._execute_rcon = AsyncMock(  # type: ignore[method-assign]
        return_value=(f"USAPO_EMERALD_EXCHANGE_RESULT|2|{REQUEST_ID}|completed|32|1|new")
    )
    interaction = SimpleNamespace(user=SimpleNamespace(id=123))

    result = asyncio.run(
        bot.confirm_emerald_diamond_exchange(  # type: ignore[arg-type]
            interaction,
            request_id=REQUEST_ID,
            emerald_count=32,
        )
    )

    assert result is not None
    assert result.status == "completed"
    bot._online_exchange_account.assert_awaited_once_with(123)  # type: ignore[attr-defined]
    bot._execute_rcon.assert_awaited_once_with(  # type: ignore[attr-defined]
        f"usapo-event-bridge emerald-diamond-v2 {PLAYER_UUID} 32 {REQUEST_ID}"
    )


def test_structured_exchange_log_wires_uuid_to_discord_audit(tmp_path) -> None:
    class OneLineTailer:
        def __init__(self, line: PendingLine) -> None:
            self.line = line
            self.acknowledged: list[PendingLine] = []

        async def lines(self):  # type: ignore[no-untyped-def]
            yield self.line

        def acknowledge(self, line: PendingLine) -> None:
            self.acknowledged.append(line)

    bot = MinecraftDiscordBot(Config(discord_token="test", accounts_path=tmp_path / "accounts.db"))
    bot._accounts.initialize()
    bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
        player_uuid=PLAYER_UUID,
    )
    bot._settings = RuntimeSettings(guild_id=456)
    line = PendingLine(
        text=(
            "[12:34:56] [Server thread/INFO]: [UsapoEventBridge] "
            f"USAPO_EMERALD_EXCHANGE|2|{REQUEST_ID}|{PLAYER_UUID}"
            "|U3RldmU|32|1|1786406400000"
        ),
        cursor=Cursor("log-1", 123),
    )
    tailer = OneLineTailer(line)
    bot._tailer = tailer  # type: ignore[assignment]
    bot.wait_until_ready = AsyncMock()  # type: ignore[method-assign]
    bot._send = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    bot._send.assert_awaited_once()  # type: ignore[attr-defined]
    embed = bot._send.await_args.args[0]  # type: ignore[attr-defined]
    assert "Steve (<@123>)" in str(embed.description)
    assert "エメラルド x32" in str(embed.description)
    assert "ダイヤモンド x1" in str(embed.description)
