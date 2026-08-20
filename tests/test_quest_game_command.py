import asyncio
import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.settings import RuntimeSettings
from mc_bot.tailer import Cursor, PendingLine

OWNER_UUID = "22222222-2222-4222-8222-222222222222"
WORKER_UUID = "33333333-3333-4333-8333-333333333333"
EVENT_ID = "11111111-1111-4111-8111-111111111111"
CREATED_ID = "44444444-4444-4444-8444-444444444444"
ACCEPTED_ID = "55555555-5555-4555-8555-555555555555"


class LineTailer:
    def __init__(self, lines: list[PendingLine]) -> None:
        self.pending = lines
        self.acknowledged: list[PendingLine] = []

    async def lines(self):  # type: ignore[no-untyped-def]
        for line in self.pending:
            yield line

    def acknowledge(self, line: PendingLine) -> None:
        self.acknowledged.append(line)


class QuestRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        fields = command.split()
        if fields[1] == "quest-invalidate":
            return f"USAPO_QUEST_ACTION_RESULT|1|{fields[4]}|{fields[2]}|completed|cancelled|new"
        raise AssertionError(f"unexpected RCON command: {command}")


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _line(
    *,
    offset: int,
    transition_id: str,
    kind: str,
    status: str,
    worker_uuid: str = "-",
    worker_name: str = "-",
    accepted_deadline: int = 0,
    published_offset_seconds: int = 0,
) -> PendingLine:
    created = datetime(2026, 8, 20, tzinfo=UTC)
    created_ms = int(created.timestamp() * 1_000)
    open_expiry = int((created + timedelta(days=7)).timestamp() * 1_000)
    published = int((created + timedelta(seconds=published_offset_seconds)).timestamp() * 1_000)
    message = (
        f"USAPO_QUEST_STATE|1|{transition_id}|{kind}|17|{EVENT_ID}|{OWNER_UUID}|"
        f"{_encode('Owner')}|{worker_uuid}|{worker_name}|"
        f"{_encode('minecraft:ancient_debris')}|{_encode('古代の残骸')}|8|"
        f"{_encode('minecraft:diamond')}|{_encode('ダイヤモンド')}|3|24|{status}|"
        f"{open_expiry}|{accepted_deadline}|{created_ms}|{published}"
    )
    return PendingLine(
        text=f"[08:24:00] [Server thread/INFO]: [UsapoEventBridge] {message}",
        cursor=Cursor("log-1", offset),
    )


def test_game_quest_wires_owner_and_worker_to_the_correct_accounts(tmp_path) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="test", accounts_path=tmp_path / "accounts.db"))
    bot._accounts.initialize()
    bot._quests.initialize()
    owner = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Owner",
        server_player_name="Owner",
        discord_user_id=2002,
        discord_username="owner",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=OWNER_UUID,
    )
    worker = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Worker",
        server_player_name="Worker",
        discord_user_id=2003,
        discord_username="worker",
        source="self",
        status="active",
        created_by=2003,
        player_uuid=WORKER_UUID,
    )
    created = datetime(2026, 8, 20, tzinfo=UTC)
    deadline = int((created + timedelta(days=1)).timestamp() * 1_000)
    lines = [
        _line(offset=100, transition_id=CREATED_ID, kind="created", status="open"),
        _line(
            offset=200,
            transition_id=ACCEPTED_ID,
            kind="accepted",
            status="accepted",
            worker_uuid=WORKER_UUID,
            worker_name=_encode("Worker"),
            accepted_deadline=deadline,
            published_offset_seconds=1,
        ),
    ]
    tailer = LineTailer(lines)
    bot._tailer = tailer  # type: ignore[assignment]
    bot._refresh_quest_listing = AsyncMock()  # type: ignore[method-assign]
    bot._deliver_quest_logs = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(bot._forward_logs())

    quest = bot._quests.get(17)
    assert tailer.acknowledged == lines
    assert quest is not None
    assert quest.owner_account_id == owner.id
    assert quest.owner_discord_user_id == 2002
    assert quest.worker_account_id == worker.id
    assert quest.worker_discord_user_id == 2003
    assert quest.status == "accepted"
    assert bot._refresh_quest_listing.await_count == 2  # type: ignore[attr-defined]


def test_unlinked_quest_is_audited_and_cancelled_with_owner_uuid(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    bot._quests.initialize()
    bot._settings = RuntimeSettings(guild_id=1001)
    bot._rcon = QuestRcon()  # type: ignore[assignment]
    bot._send_minecraft_private_message = AsyncMock()  # type: ignore[method-assign]
    event_line = _line(
        offset=100,
        transition_id=CREATED_ID,
        kind="created",
        status="open",
    )
    tailer = LineTailer([event_line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    rcon = bot._rcon
    assert isinstance(rcon, QuestRcon)
    assert len(rcon.commands) == 1
    fields = rcon.commands[0].split()
    assert fields[:4] == ["usapo-event-bridge", "quest-invalidate", "17", OWNER_UUID]
    quest = bot._quests.get(17)
    assert quest is not None
    assert quest.status == "open"
    assert quest.owner_account_id is None
    assert quest.discord_message_id is None
    assert tailer.acknowledged == [event_line]
    bot._send_minecraft_private_message.assert_awaited_once()  # type: ignore[attr-defined]


def test_stale_unlinked_creation_does_not_block_later_completed_state(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    bot._quests.initialize()
    bot._settings = RuntimeSettings(guild_id=1001)
    bot._rcon = QuestRcon()  # type: ignore[assignment]
    bot._rcon.execute = lambda command: (  # type: ignore[method-assign]
        f"USAPO_QUEST_ACTION_RESULT|1|{command.split()[4]}|17|completed|completed|duplicate"
    )
    bot._send_minecraft_private_message = AsyncMock()  # type: ignore[method-assign]
    bot._refresh_quest_listing = AsyncMock()  # type: ignore[method-assign]
    bot._deliver_quest_logs = AsyncMock()  # type: ignore[method-assign]
    created = datetime(2026, 8, 20, tzinfo=UTC)
    deadline = int((created + timedelta(days=1)).timestamp() * 1_000)
    lines = [
        _line(offset=100, transition_id=CREATED_ID, kind="created", status="open"),
        _line(
            offset=200,
            transition_id=ACCEPTED_ID,
            kind="completed",
            status="completed",
            worker_uuid=WORKER_UUID,
            worker_name=_encode("Worker"),
            accepted_deadline=deadline,
            published_offset_seconds=1,
        ),
    ]
    tailer = LineTailer(lines)
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    quest = bot._quests.get(17)
    assert tailer.acknowledged == lines
    assert quest is not None
    assert quest.status == "completed"
    bot._send_minecraft_private_message.assert_not_awaited()  # type: ignore[attr-defined]
