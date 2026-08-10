import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mc_bot.activity import ActivityKind, MinecraftActivityEvent
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import (
    MinecraftLevelUpEvent,
    MinecraftVoiceHeartbeatResult,
    MinecraftXpExchangeEvent,
)
from mc_bot.rcon import RconError
from mc_bot.settings import RuntimeSettings
from mc_bot.tailer import Cursor, PendingLine


class ExperienceRcon:
    def __init__(self) -> None:
        self.level = 1
        self.points = 0
        self.add_error_response: str | None = None
        self.add_exception: Exception | None = None
        self.tellraw_failures = 0
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("usapo-event-bridge voice-bonus "):
            return "Voice XP bonus state updated"
        if command.startswith("tellraw @a "):
            if self.tellraw_failures > 0:
                self.tellraw_failures -= 1
                raise OSError("tellraw connection lost")
            return ""
        if command.startswith("experience add Steve ") and command.endswith(" points"):
            if self.add_exception is not None:
                raise self.add_exception
            if self.add_error_response is not None:
                return self.add_error_response
            added = int(command.split()[3])
            self.points += added
            return f"Added {added} experience points to Steve"
        if command == "list":
            return "There are 1 of a max of 20 players online: Steve"
        if command.startswith("title Steve actionbar "):
            return ""
        if command.startswith("playsound minecraft:entity.experience_orb.pickup player Steve "):
            return "Played sound to Steve"
        if command == "experience query Steve levels":
            return f"Steve has {self.level} experience levels"
        if command == "experience query Steve points":
            return f"Steve has {self.points} experience points"
        raise AssertionError(f"unexpected RCON command: {command}")


PLAYER_UUID = "8667ba71-b85a-4004-af54-457a9734eed7"


def activity(
    kind: ActivityKind, index: int, second: int, *, amount: int = 1
) -> MinecraftActivityEvent:
    return MinecraftActivityEvent(
        event_id=f"00000000-0000-0000-0000-{index:012d}",
        kind=kind,
        player_uuid=PLAYER_UUID,
        player_name="Steve",
        amount=amount,
        occurred_at=f"2026-08-11T00:00:{second:02d}+00:00",
    )


def test_fishing_combo_rewards_minecraft_xp_with_private_actionbar_only(
    tmp_path,
) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=7,
        observed_at="2026-08-09T00:00:00+00:00",
    )
    send_audit = AsyncMock(return_value=True)
    bot._level_bot_xp.send_fishing_combo = send_audit  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._record_activity_event(activity(ActivityKind.FISHING, 1, 1))
        await bot._record_activity_event(activity(ActivityKind.FISHING, 2, 2))
        await bot._deliver_pending_activity_events()

    asyncio.run(exercise())

    assert rcon.points == 7
    assert sum(command == "experience add Steve 2 points" for command in rcon.commands) == 1
    assert sum(command == "experience add Steve 5 points" for command in rcon.commands) == 1
    private_messages = [
        command for command in rcon.commands if command.startswith("title Steve actionbar ")
    ]
    assert len(private_messages) == 2
    assert "釣りボーナス! +2 XP" in private_messages[0]
    assert "連続釣り2回! +5 XP" in private_messages[1]
    assert not any(command.startswith("tellraw ") for command in rcon.commands)
    assert not any(command.startswith("playsound ") for command in rcon.commands)
    assert send_audit.await_count == 2
    assert bot._accounts.list_pending_fishing_audits() == []
    assert bot._accounts.list_pending_fishing_reward_deliveries() == []
    assert not any(
        command == "list" or command.startswith("scoreboard ") for command in rcon.commands
    )


def test_public_fishing_milestone_replaces_private_actionbar_and_stays_silent(
    tmp_path,
) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-09T00:00:00+00:00",
    )
    bot._level_bot_xp.send_fishing_combo = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]

    async def exercise() -> None:
        for index in range(1, 11):
            await bot._record_activity_event(activity(ActivityKind.FISHING, index, index))
        await bot._deliver_pending_activity_events()

    asyncio.run(exercise())

    actionbar_commands = [
        command for command in rcon.commands if command.startswith("title Steve actionbar ")
    ]
    private_messages = [command for command in actionbar_commands if '"text":""' not in command]
    clears = [command for command in actionbar_commands if '"text":""' in command]
    public_messages = [command for command in rcon.commands if command.startswith("tellraw @a ")]
    assert len(private_messages) == 9
    assert len(clears) == 1
    assert not any("10コンボ" in command for command in private_messages)
    assert len(public_messages) == 1
    assert "10コンボ" in public_messages[0]
    assert "+15 XP" in public_messages[0]
    assert not any(command.startswith("playsound ") for command in rcon.commands)
    send_log.assert_awaited_once()
    assert "10コンボ" in send_log.await_args.args[0].description
    assert bot._accounts.list_pending_fishing_public_deliveries() == []


def test_woodcutting_combo_rewards_with_private_actionbar_and_xp_sound(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=7,
        observed_at="2026-08-11T00:00:00+00:00",
    )
    send_audit = AsyncMock(return_value=True)
    bot._level_bot_xp.send_woodcutting_combo = send_audit  # type: ignore[method-assign]

    async def exercise() -> None:
        for index in range(1, 6):
            await bot._record_activity_event(activity(ActivityKind.WOODCUTTING, index, index))
        await bot._deliver_pending_activity_events()

    asyncio.run(exercise())

    assert rcon.points == 5
    assert rcon.commands.count("experience add Steve 5 points") == 1
    private_messages = [
        command for command in rcon.commands if command.startswith("title Steve actionbar ")
    ]
    assert len(private_messages) == 1
    assert "連続伐採5本! +5 XP" in private_messages[0]
    assert (
        rcon.commands.count(
            "playsound minecraft:entity.experience_orb.pickup player Steve ~ ~ ~ 1 1"
        )
        == 1
    )
    assert not any(command.startswith("tellraw ") for command in rcon.commands)
    send_audit.assert_awaited_once()
    assert bot._accounts.list_pending_woodcutting_audits() == []
    assert bot._accounts.list_pending_woodcutting_reward_deliveries() == []


def test_failed_fishing_delivery_does_not_block_woodcutting_delivery(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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

    async def exercise() -> None:
        await bot._record_activity_event(activity(ActivityKind.FISHING, 1, 1))
        for index in range(2, 7):
            await bot._record_activity_event(activity(ActivityKind.WOODCUTTING, index, index))

        bot._grant_fishing_combo_reward = AsyncMock(  # type: ignore[method-assign]
            side_effect=OSError("fishing RCON connection lost")
        )
        bot._grant_woodcutting_combo_reward = AsyncMock()  # type: ignore[method-assign]
        bot._deliver_fishing_public_announcements = AsyncMock()  # type: ignore[method-assign]
        bot._deliver_fishing_combo_audits = AsyncMock()  # type: ignore[method-assign]
        bot._deliver_woodcutting_public_announcements = AsyncMock()  # type: ignore[method-assign]
        bot._deliver_woodcutting_combo_audits = AsyncMock()  # type: ignore[method-assign]

        await bot._deliver_pending_activity_events()

        bot._grant_fishing_combo_reward.assert_awaited_once()  # type: ignore[attr-defined]
        bot._grant_woodcutting_combo_reward.assert_awaited_once()  # type: ignore[attr-defined]
        await bot.close()

    asyncio.run(exercise())


def test_public_woodcutting_milestone_replaces_actionbar_but_keeps_private_sound(
    tmp_path,
) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-11T00:00:00+00:00",
    )
    bot._level_bot_xp.send_woodcutting_combo = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    send_log = AsyncMock()
    rcon.tellraw_failures = 1
    send_log.side_effect = [RuntimeError("Discord unavailable"), None]
    bot._send = send_log  # type: ignore[method-assign]
    pending_after_failure = []

    async def exercise() -> None:
        for index in range(1, 21):
            await bot._record_activity_event(activity(ActivityKind.WOODCUTTING, index, index))
        await bot._deliver_pending_activity_events()
        pending_after_failure.extend(bot._accounts.list_pending_woodcutting_public_deliveries())
        await bot._deliver_woodcutting_public_announcements()

    asyncio.run(exercise())

    actionbar_commands = [
        command for command in rcon.commands if command.startswith("title Steve actionbar ")
    ]
    private_messages = [command for command in actionbar_commands if '"text":""' not in command]
    clears = [command for command in actionbar_commands if '"text":""' in command]
    public_messages = [command for command in rcon.commands if command.startswith("tellraw @a ")]
    sounds = [command for command in rcon.commands if command.startswith("playsound ")]
    assert len(private_messages) == 2
    assert len(clears) == 1
    assert not any("20本" in command for command in private_messages)
    assert len(public_messages) == 2
    assert all("連続伐採" in command for command in public_messages)
    assert all("20本" in command for command in public_messages)
    assert all("+30 XP" in command for command in public_messages)
    assert len(sounds) == 3
    assert rcon.points == 50
    assert all(" player Steve " in command for command in sounds)
    sound_indices = [
        index for index, command in enumerate(rcon.commands) if command.startswith("playsound ")
    ]
    public_indices = [
        index for index, command in enumerate(rcon.commands) if command.startswith("tellraw @a ")
    ]
    assert max(sound_indices) < min(public_indices)
    assert len(pending_after_failure) == 1
    assert not pending_after_failure[0].minecraft_public_delivered
    assert not pending_after_failure[0].discord_public_delivered
    assert send_log.await_count == 2
    assert "20本" in send_log.await_args.args[0].description
    assert "+30 XP" in send_log.await_args.args[0].description
    assert bot._accounts.list_pending_woodcutting_public_deliveries() == []


class OneLineTailer:
    def __init__(self, line: PendingLine) -> None:
        self._line = line
        self.acknowledged: list[PendingLine] = []

    async def lines(self):  # type: ignore[no-untyped-def]
        yield self._line

    def acknowledge(self, line: PendingLine) -> None:
        self.acknowledged.append(line)


def test_structured_fishing_log_wires_uuid_to_reward_without_scoreboard_poll(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="OldName",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
        player_uuid=PLAYER_UUID,
    )
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-11T00:00:00+00:00",
    )
    bot._settings = RuntimeSettings(guild_id=456)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._level_bot_xp.send_fishing_combo = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    pending = PendingLine(
        text=(
            "[00:00:01] [Server thread/INFO]: [UsapoEventBridge] USAPO_ACTIVITY|1|"
            "00000000-0000-0000-0000-000000000001|fishing|"
            f"{PLAYER_UUID}|TmV3TmFtZQ|1|1786406401000"
        ),
        cursor=Cursor("log-1", 123),
    )
    tailer = OneLineTailer(pending)
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [pending]
    assert rcon.commands.count("experience add Steve 2 points") == 1
    assert any("釣りボーナス! +2 XP" in command for command in rcon.commands)
    assert not any(
        command == "list" or command.startswith("scoreboard ") for command in rcon.commands
    )


def test_structured_experience_log_wires_uuid_and_amount_without_xp_poll(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    bot._accounts.create_registration(
        edition="java",
        minecraft_name="OldName",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
        player_uuid=PLAYER_UUID,
    )
    bot._settings = RuntimeSettings(guild_id=456)
    send = AsyncMock(return_value=True)
    bot._level_bot_xp.send = send  # type: ignore[method-assign]
    pending = PendingLine(
        text=(
            "[00:00:01] [Server thread/INFO]: [UsapoEventBridge] USAPO_ACTIVITY|1|"
            "00000000-0000-0000-0000-000000000099|experience|"
            f"{PLAYER_UUID}|TmV3TmFtZQ|37|1786406401000"
        ),
        cursor=Cursor("log-1", 456),
    )
    tailer = OneLineTailer(pending)
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [pending]
    send.assert_awaited_once()
    event = send.await_args.args[0]
    assert event.minecraft_xp == 37
    assert event.discord_user_id == 123
    assert event.account_id == 1
    assert bot._accounts.list_minecraft_xp_outbox() == []


def test_sync_delivers_online_minecraft_xp_exchange_once(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    bot._settings = RuntimeSettings(guild_id=456)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    event = MinecraftXpExchangeEvent(
        id=7,
        event_id="exchange-7",
        guild_id=456,
        user_id=123,
        minecraft_account_id=f"mc-bot:{account.id}",
        cost_xp=10,
        reward_xp=100,
        status="pending",
    )
    bot._level_bot_xp.fetch_xp_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    update = AsyncMock(return_value=True)
    bot._level_bot_xp.update_xp_exchange = update  # type: ignore[method-assign]
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]

    asyncio.run(
        bot._sync_minecraft_xp_exchanges(
            guild_id=456,
            online_names={"steve"},
            linked_accounts=(account,),
        )
    )

    assert update.await_args_list[0].args == (7, 456, "claim")
    claim_token = update.await_args_list[0].kwargs["claim_token"]
    assert update.await_args_list[1].args == (7, 456, "complete")
    assert update.await_args_list[1].kwargs["claim_token"] == claim_token
    assert "experience add Steve 100 points" in rcon.commands
    assert sum(command.startswith("experience add Steve ") for command in rcon.commands) == 1
    assert bot._accounts.has_minecraft_xp_exchange_delivery("exchange-7")
    delivery = bot._accounts.get_minecraft_xp_exchange_delivery("exchange-7")
    assert delivery is not None
    assert delivery.reward_applied
    assert delivery.level_completed
    assert delivery.minecraft_notified
    assert delivery.discord_notified
    send_log.assert_awaited_once()


def test_sync_cancels_exchange_when_player_is_offline(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    bot._rcon = ExperienceRcon()  # type: ignore[assignment]
    bot._level_bot_xp.fetch_xp_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            MinecraftXpExchangeEvent(
                id=8,
                event_id="exchange-8",
                guild_id=456,
                user_id=123,
                minecraft_account_id=f"mc-bot:{account.id}",
                cost_xp=10,
                reward_xp=100,
                status="pending",
            )
        ]
    )
    update = AsyncMock(return_value=True)
    bot._level_bot_xp.update_xp_exchange = update  # type: ignore[method-assign]

    asyncio.run(
        bot._sync_minecraft_xp_exchanges(
            guild_id=456,
            online_names=set(),
            linked_accounts=(account,),
        )
    )

    update.assert_awaited_once_with(8, 456, "cancel", claim_token=None)


def test_sync_retries_lost_claim_response_with_same_owner_token(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    bot._rcon = ExperienceRcon()  # type: ignore[assignment]
    pending = MinecraftXpExchangeEvent(
        id=9,
        event_id="exchange-9",
        guild_id=456,
        user_id=123,
        minecraft_account_id=f"mc-bot:{account.id}",
        cost_xp=10,
        reward_xp=50,
        status="pending",
    )
    delivering = MinecraftXpExchangeEvent(
        id=9,
        event_id="exchange-9",
        guild_id=456,
        user_id=123,
        minecraft_account_id=f"mc-bot:{account.id}",
        cost_xp=10,
        reward_xp=50,
        status="delivering",
    )
    bot._level_bot_xp.fetch_xp_exchanges = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[pending], [delivering]]
    )
    update = AsyncMock(side_effect=[False, True, True])
    bot._level_bot_xp.update_xp_exchange = update  # type: ignore[method-assign]
    bot._send = AsyncMock()  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._sync_minecraft_xp_exchanges(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )
        await bot._sync_minecraft_xp_exchanges(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )

    asyncio.run(exercise())

    first_token = update.await_args_list[0].kwargs["claim_token"]
    assert update.await_args_list[1].kwargs["claim_token"] == first_token
    assert (
        sum(
            command.startswith("experience add Steve ")
            for command in bot._rcon.commands  # type: ignore[union-attr]
        )
        == 1
    )


def test_sync_retries_completion_and_each_notification(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    rcon = ExperienceRcon()
    rcon.tellraw_failures = 1
    bot._rcon = rcon  # type: ignore[assignment]
    event = MinecraftXpExchangeEvent(
        id=10,
        event_id="exchange-10",
        guild_id=456,
        user_id=123,
        minecraft_account_id=f"mc-bot:{account.id}",
        cost_xp=10,
        reward_xp=50,
        status="pending",
    )
    bot._level_bot_xp.fetch_xp_exchanges = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[event], []]
    )
    bot._level_bot_xp.update_xp_exchange = AsyncMock(  # type: ignore[method-assign]
        side_effect=[True, False, True]
    )
    send_log = AsyncMock(side_effect=[RuntimeError("Discord unavailable"), None])
    bot._send = send_log  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._sync_minecraft_xp_exchanges(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )
        await bot._sync_minecraft_xp_exchanges(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )

    asyncio.run(exercise())

    assert sum(command.startswith("experience add Steve ") for command in rcon.commands) == 1
    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 2
    assert send_log.await_count == 2
    delivery = bot._accounts.get_minecraft_xp_exchange_delivery("exchange-10")
    assert delivery is not None
    assert delivery.level_completed
    assert delivery.minecraft_notified
    assert delivery.discord_notified


def test_sync_does_not_charge_ambiguous_rcon_delivery(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    rcon = ExperienceRcon()
    rcon.add_exception = OSError("RCON response lost")
    bot._rcon = rcon  # type: ignore[assignment]
    event = MinecraftXpExchangeEvent(
        id=11,
        event_id="exchange-11",
        guild_id=456,
        user_id=123,
        minecraft_account_id=f"mc-bot:{account.id}",
        cost_xp=10,
        reward_xp=50,
        status="pending",
    )
    delivering = MinecraftXpExchangeEvent(
        id=11,
        event_id="exchange-11",
        guild_id=456,
        user_id=123,
        minecraft_account_id=f"mc-bot:{account.id}",
        cost_xp=10,
        reward_xp=50,
        status="delivering",
    )
    bot._level_bot_xp.fetch_xp_exchanges = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[event], [delivering]]
    )
    update = AsyncMock(return_value=True)
    bot._level_bot_xp.update_xp_exchange = update  # type: ignore[method-assign]
    bot._send = AsyncMock()  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._sync_minecraft_xp_exchanges(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )
        await bot._sync_minecraft_xp_exchanges(
            guild_id=456, online_names=set(), linked_accounts=(account,)
        )

    asyncio.run(exercise())

    assert update.await_count == 2
    assert all(call.args == (11, 456, "claim") for call in update.await_args_list)
    delivery = bot._accounts.get_minecraft_xp_exchange_delivery("exchange-11")
    assert delivery is not None
    assert not delivery.reward_applied
    assert not delivery.level_completed
    bot._send.assert_not_awaited()  # type: ignore[attr-defined]


def test_natural_xp_event_delivers_exact_gain_without_experience_queries(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    send = AsyncMock(return_value=True)
    bot._level_bot_xp.send = send  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, False)
    )

    async def exercise() -> None:
        assert await bot._record_activity_event(activity(ActivityKind.EXPERIENCE, 99, 1, amount=5))
        await bot._deliver_pending_activity_events()

    asyncio.run(exercise())

    send.assert_awaited_once()
    event = send.await_args.args[0]
    assert event.minecraft_xp == 5
    assert event.discord_user_id == 123
    assert event.guild_id == 456
    assert bot._accounts.list_minecraft_xp_outbox() == []
    assert not any(
        command == "list" or command.startswith("experience query ") for command in rcon.commands
    )


def test_sync_announces_voice_bonus_start_once_with_cooldown(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._online_player_names = {"steve"}
    bot._linked_accounts_by_online_name = AsyncMock(  # type: ignore[method-assign]
        return_value={"steve": account}
    )
    bot._level_bot_xp.fetch_xp_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[]
    )
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[]
    )
    bot._level_bot_xp.send = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            MinecraftVoiceHeartbeatResult(0, True),
            MinecraftVoiceHeartbeatResult(30, True),
            MinecraftVoiceHeartbeatResult(0, False),
            MinecraftVoiceHeartbeatResult(0, True),
        ]
    )
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._sync_minecraft_xp()
        send_log.assert_not_awaited()
        assert not any(command.startswith("tellraw @a ") for command in rcon.commands)
        await bot._sync_minecraft_xp()
        send_log.assert_not_awaited()
        await bot._sync_minecraft_xp()
        send_log.assert_not_awaited()
        await bot._sync_minecraft_xp()

    asyncio.run(exercise())

    tellraw_commands = [command for command in rcon.commands if command.startswith("tellraw @a ")]
    assert len(tellraw_commands) == 1
    assert "うさぽサーバー" in tellraw_commands[0]
    assert "VC XPとMinecraft内の経験値が2倍" in tellraw_commands[0]
    send_log.assert_awaited_once()
    assert send_log.await_args.args[0].description == (
        "🎮🔊 **[うさぽサーバー] Steve (<@123>) さん** が"
        "MinecraftとVCに同時接続したので、"
        "**VC XPとMinecraft内の経験値が2倍**になりました!"
    )


def test_sync_doubles_in_game_xp_once_while_voice_bonus_is_active(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    rcon.level = 0
    bot._rcon = rcon  # type: ignore[assignment]
    bot._online_player_names = {"steve"}
    bot._linked_accounts_by_online_name = AsyncMock(  # type: ignore[method-assign]
        return_value={"steve": account}
    )
    bot._level_bot_xp.fetch_xp_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[]
    )
    bot._level_bot_xp.fetch_resource_exchanges = AsyncMock(  # type: ignore[method-assign]
        return_value=[]
    )
    bot._level_bot_xp.send = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(30, True)
    )
    bot._send = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._sync_minecraft_xp()
        await bot._sync_minecraft_xp()
        await bot._sync_minecraft_xp()

    asyncio.run(exercise())

    assert rcon.commands.count(f"usapo-event-bridge voice-bonus {PLAYER_UUID} on") == 1
    assert not any(command.startswith("experience query ") for command in rcon.commands)
    assert not any(command.startswith("experience add Steve ") for command in rcon.commands)
    assert "list" not in rcon.commands
    bot._level_bot_xp.send.assert_not_awaited()  # type: ignore[attr-defined]


def test_online_player_cache_uses_one_startup_list_query() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="test", rcon_password="test"))
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._refresh_online_player_cache())

    assert rcon.commands == ["list"]
    assert bot._online_player_names == {"steve"}


def test_voice_bonus_activation_is_sent_to_paper_without_xp_query(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    rcon.level = 0
    bot._rcon = rcon  # type: ignore[assignment]
    bot._level_bot_xp.send = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, True)
    )
    bot._send = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._sync_voice_bonus_for_account(account)

    asyncio.run(exercise())

    assert rcon.commands[0] == f"usapo-event-bridge voice-bonus {PLAYER_UUID} on"
    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 1
    assert not any(command.startswith("experience query ") for command in rcon.commands)


def test_voice_bonus_deactivation_is_sent_to_paper_without_xp_query(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._voice_bonus_active_users.add(123)
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, False)
    )

    asyncio.run(bot._sync_voice_bonus_for_account(account))

    assert rcon.commands == [f"usapo-event-bridge voice-bonus {PLAYER_UUID} off"]
    assert not any(command.startswith("experience query ") for command in rcon.commands)
    assert 123 not in bot._voice_bonus_active_users


def test_failed_paper_voice_bonus_activation_remains_retryable(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
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
    execute = AsyncMock(side_effect=[RconError("plugin unavailable"), None])
    bot._execute_checked_rcon = execute  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._set_voice_bonus_state(account, active=True, notify=False)
        assert 123 not in bot._voice_bonus_active_users
        await bot._set_voice_bonus_state(account, active=True, notify=False)

    asyncio.run(exercise())

    assert execute.await_count == 2
    assert 123 in bot._voice_bonus_active_users


def test_failed_xp_bonus_restores_observation_and_logs_warning(tmp_path, caplog) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    rcon = ExperienceRcon()
    rcon.level = 0
    rcon.points = 3
    rcon.add_error_response = "No player was found"
    bot._rcon = rcon  # type: ignore[assignment]
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-04T00:00:00+00:00",
    )

    async def exercise() -> None:
        await bot._observe_minecraft_xp_for_account(
            account,
            guild_id=456,
            observed_at="2026-08-04T00:00:30+00:00",
            double_in_game_xp=True,
        )
        rcon.add_error_response = None
        rcon.points = 5
        await bot._observe_minecraft_xp_for_account(
            account,
            guild_id=456,
            observed_at="2026-08-04T00:01:00+00:00",
            double_in_game_xp=True,
        )

    asyncio.run(exercise())

    bonus_commands = [
        command for command in rcon.commands if command.startswith("experience add Steve ")
    ]
    assert bonus_commands == [
        "experience add Steve 3 points",
        "experience add Steve 2 points",
    ]
    assert rcon.points == 7
    assert "Could not apply doubled Minecraft XP to Steve" in caplog.text


def test_concurrent_xp_observations_do_not_double_the_same_gain(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    rcon = ExperienceRcon()
    rcon.level = 0
    rcon.points = 3
    bot._rcon = rcon  # type: ignore[assignment]
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-04T00:00:00+00:00",
    )

    async def exercise() -> None:
        await asyncio.gather(
            bot._observe_minecraft_xp_for_account(
                account,
                guild_id=456,
                observed_at="2026-08-04T00:00:30+00:00",
                double_in_game_xp=True,
            ),
            bot._observe_minecraft_xp_for_account(
                account,
                guild_id=456,
                observed_at="2026-08-04T00:00:30+00:00",
                double_in_game_xp=True,
            ),
        )

    asyncio.run(exercise())

    bonus_commands = [
        command for command in rcon.commands if command.startswith("experience add Steve ")
    ]
    assert bonus_commands == ["experience add Steve 3 points"]
    assert rcon.points == 6


def test_advancement_keeps_original_log_then_sends_reward_everywhere(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    )
    bot._settings = RuntimeSettings(guild_id=456)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    line = PendingLine(
        "[12:34:56] [Server thread/INFO]: Steve has made the advancement [Stone Age]",
        Cursor("device:inode", 100),
    )
    tailer = OneLineTailer(line)
    bot._tailer = tailer  # type: ignore[assignment]
    send_xp = AsyncMock(return_value=True)
    bot._level_bot_xp.send = send_xp  # type: ignore[method-assign]
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]
    bot.wait_until_ready = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    guild.get_member.return_value = None
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    asyncio.run(bot._forward_logs())

    sent_event = send_xp.await_args.args[0]
    assert sent_event.minecraft_xp == 10_000
    assert sent_event.discord_user_id == 123
    assert len(send_log.await_args_list) == 2
    original = send_log.await_args_list[0].args[0]
    reward = send_log.await_args_list[1].args[0]
    assert original.description == ("🏆 **Steve (<@123>) さん** が進捗「石器時代」を達成しました")
    assert reward.description == (
        "✨ **[うさぽサーバー] Steve (<@123>) さん** が進捗「石器時代」を"
        "達成したので、サーバーでの **100 XP**とMinecraft内の "
        "**100 XP**を獲得しました!"
    )
    assert rcon.commands[0] == "experience add Steve 100 points"
    assert rcon.commands[1].startswith("tellraw @a ")
    assert "うさぽサーバー" in rcon.commands[1]
    assert rcon.commands[1].count("100 XP") == 2
    assert rcon.points == 100
    assert tailer.acknowledged == [line]
    assert bot._accounts.list_minecraft_xp_outbox() == []


def test_advancement_without_rcon_only_announces_server_xp(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    )
    bot._settings = RuntimeSettings(guild_id=456)
    line = PendingLine(
        "[12:34:56] [Server thread/INFO]: Steve has made the advancement [Stone Age]",
        Cursor("device:inode", 100),
    )
    tailer = OneLineTailer(line)
    bot._tailer = tailer  # type: ignore[assignment]
    bot._level_bot_xp.send = AsyncMock(return_value=True)  # type: ignore[method-assign]
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]
    bot.wait_until_ready = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    guild.get_member.return_value = None
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    asyncio.run(bot._forward_logs())

    reward = send_log.await_args_list[1].args[0]
    assert reward.description == (
        "✨ **[うさぽサーバー] Steve (<@123>) さん** が進捗「石器時代」を"
        "達成したので、サーバーでの **100 XP**を獲得しました!"
    )
    assert tailer.acknowledged == [line]


def test_advancement_minecraft_reward_is_not_granted_twice(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    reward = bot._accounts.claim_advancement_reward(
        event_id="advancement-event-1",
        account_id=account.id,
        advancement="Stone Age",
        discord_user_id=123,
        guild_id=456,
        minecraft_xp=10_000,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    assert reward is not None
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    rcon = ExperienceRcon()
    rcon.level = 0
    bot._rcon = rcon  # type: ignore[assignment]

    async def exercise() -> None:
        for _ in range(2):
            await bot._grant_advancement_minecraft_reward(
                account,
                event_id=reward.event_id,
                observed_at=reward.observed_at,
            )

    asyncio.run(exercise())

    assert rcon.commands == ["experience add Steve 100 points"]
    assert rcon.points == 100
    assert bot._accounts.is_advancement_minecraft_reward_delivered(reward.event_id)
    assert (
        bot._accounts.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=100,
            observed_at="2026-08-04T00:00:30+00:00",
            double_in_game_xp=True,
        )
        is None
    )


def test_advancement_minecraft_reward_retries_after_explicit_command_failure(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    reward = bot._accounts.claim_advancement_reward(
        event_id="advancement-event-1",
        account_id=account.id,
        advancement="Stone Age",
        discord_user_id=123,
        guild_id=456,
        minecraft_xp=10_000,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    assert reward is not None
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at=reward.observed_at,
    )
    rcon = ExperienceRcon()
    rcon.add_error_response = "No player was found"
    bot._rcon = rcon  # type: ignore[assignment]

    with pytest.raises(ValueError):
        asyncio.run(
            bot._grant_advancement_minecraft_reward(
                account,
                event_id=reward.event_id,
                observed_at=reward.observed_at,
            )
        )

    assert not bot._accounts.is_advancement_minecraft_reward_delivered(reward.event_id)
    rcon.add_error_response = None
    asyncio.run(
        bot._grant_advancement_minecraft_reward(
            account,
            event_id=reward.event_id,
            observed_at=reward.observed_at,
        )
    )
    assert rcon.points == 100


def test_minecraft_leave_sends_final_voice_heartbeat_before_discord_log(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    )
    bot._settings = RuntimeSettings(guild_id=456)
    bot._voice_bonus_active_users.add(123)
    line = PendingLine(
        "[12:34:56] [Server thread/INFO]: Steve left the game",
        Cursor("device:inode", 100),
    )
    tailer = OneLineTailer(line)
    bot._tailer = tailer  # type: ignore[assignment]
    bot.wait_until_ready = AsyncMock()  # type: ignore[method-assign]
    delivery_order: list[str] = []

    async def heartbeat(**_kwargs):  # type: ignore[no-untyped-def]
        delivery_order.append("heartbeat")
        return MinecraftVoiceHeartbeatResult(10, True)

    async def send_log(_embed):  # type: ignore[no-untyped-def]
        delivery_order.append("discord")

    bot._level_bot_xp.send_voice_heartbeat = heartbeat  # type: ignore[method-assign]
    bot._send = send_log  # type: ignore[method-assign]

    asyncio.run(bot._forward_logs())

    assert delivery_order == ["heartbeat", "discord"]
    assert 123 not in bot._voice_bonus_active_users
    assert tailer.acknowledged == [line]


def test_minecraft_join_announces_standard_server_xp_when_voice_bonus_is_inactive(
    tmp_path,
) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    )
    bot._settings = RuntimeSettings(guild_id=456)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    line = PendingLine(
        "[12:34:56] [Server thread/INFO]: Steve joined the game",
        Cursor("device:inode", 100),
    )
    tailer = OneLineTailer(line)
    bot._tailer = tailer  # type: ignore[assignment]
    bot.wait_until_ready = AsyncMock()  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, False)
    )
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    guild.get_member.return_value = None
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    asyncio.run(bot._forward_logs())

    assert [call.args[0].description for call in send_log.await_args_list] == [
        "🟢 **Steve (<@123>) さん** が参加しました",
        (
            "🎮 **[うさぽサーバー] Steve (<@123>) さん** は"
            "マイクラで遊んでいる間、**サーバーXP**を獲得します!"
        ),
    ]
    tellraw_commands = [command for command in rcon.commands if command.startswith("tellraw @a ")]
    assert len(tellraw_commands) == 1
    assert "Steve" in tellraw_commands[0]
    assert "さんはマイクラで遊んでいる間、" in tellraw_commands[0]
    assert "サーバーXP" in tellraw_commands[0]
    assert tailer.acknowledged == [line]


def test_minecraft_join_resends_active_voice_bonus_after_paper_state_loss(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        minecraft_whitelist_path=tmp_path / "whitelist.json",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    (tmp_path / "usercache.json").write_text(
        '[{"name":"Steve","uuid":"8667ba71-b85a-4004-af54-457a9734eed7"}]',
        encoding="utf-8",
    )
    bot = MinecraftDiscordBot(config)
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
    bot._voice_bonus_active_users.add(123)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    line = PendingLine(
        "[12:34:56] [Server thread/INFO]: Steve joined the game",
        Cursor("device:inode", 100),
    )
    tailer = OneLineTailer(line)
    bot._tailer = tailer  # type: ignore[assignment]
    bot.wait_until_ready = AsyncMock()  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, True)
    )
    bot._send = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock()
    guild.name = "うさぽサーバー"
    guild.get_member.return_value = None
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]

    asyncio.run(bot._forward_logs())

    assert f"usapo-event-bridge voice-bonus {PLAYER_UUID} on" in rcon.commands
    assert 123 in bot._voice_bonus_active_users
    assert tailer.acknowledged == [line]


def test_natural_xp_event_stays_in_outbox_while_api_is_blocked(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    send = AsyncMock(return_value=False)
    bot._level_bot_xp.send = send  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, False)
    )

    async def exercise() -> int:
        assert await bot._record_activity_event(activity(ActivityKind.EXPERIENCE, 100, 1, amount=5))
        await bot._deliver_pending_activity_events()
        commands_after_failure = len(rcon.commands)
        await bot._sync_minecraft_xp()
        return commands_after_failure

    commands_after_failure = asyncio.run(exercise())

    assert bot._accounts.list_minecraft_xp_outbox()
    assert len(rcon.commands) == commands_after_failure
    assert not any(command.startswith("experience query ") for command in rcon.commands)


def test_sync_announces_level_up_with_guild_name_then_acknowledges(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    )
    bot._settings = RuntimeSettings(guild_id=456)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._online_player_names = {"steve"}
    event = MinecraftLevelUpEvent(
        id=7,
        guild_id=456,
        guild_name="うさぽサーバー",
        user_id=123,
        display_name="うさぽ",
        level=10,
        minecraft_delivered=False,
        discord_delivered=False,
    )
    bot._level_bot_xp.fetch_level_ups = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    ack = AsyncMock(return_value=True)
    bot._level_bot_xp.acknowledge_level_up = ack  # type: ignore[method-assign]
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]

    asyncio.run(bot._sync_minecraft_level_up_announcements())

    assert len(rcon.commands) == 1
    assert rcon.commands[0].startswith("tellraw @a ")
    assert "うさぽサーバー" in rcon.commands[0]
    assert ack.await_args_list[0].args == (7, 456, "minecraft")
    assert ack.await_args_list[1].args == (7, 456, "discord")
    send_log.assert_awaited_once()
    embed = send_log.await_args.args[0]
    assert embed.title is None
    assert embed.description == (
        "🎉 **[うさぽサーバー] うさぽ (<@123>) さん** がレベル **10** になりました!"
    )


def test_sync_skips_minecraft_level_up_for_user_who_is_not_online(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
    bot._accounts.initialize()
    bot._accounts.create_registration(
        edition="java",
        minecraft_name="Alex",
        server_player_name="Alex",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    bot._settings = RuntimeSettings(guild_id=456)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    event = MinecraftLevelUpEvent(
        id=9,
        guild_id=456,
        guild_name="うさぽサーバー",
        user_id=123,
        display_name="うさぽ",
        level=12,
        minecraft_delivered=False,
        discord_delivered=False,
    )
    bot._level_bot_xp.fetch_level_ups = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    ack = AsyncMock(return_value=True)
    bot._level_bot_xp.acknowledge_level_up = ack  # type: ignore[method-assign]
    send_log = AsyncMock()
    bot._send = send_log  # type: ignore[method-assign]

    asyncio.run(bot._sync_minecraft_level_up_announcements())

    assert rcon.commands == []
    assert ack.await_args_list[0].args == (9, 456, "minecraft")
    assert ack.await_args_list[1].args == (9, 456, "discord")
    send_log.assert_not_awaited()


def test_sync_does_not_repeat_already_delivered_minecraft_message(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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
    )
    bot._settings = RuntimeSettings(guild_id=456)
    rcon = ExperienceRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._online_player_names = {"steve"}
    event = MinecraftLevelUpEvent(
        id=8,
        guild_id=456,
        guild_name="うさぽサーバー",
        user_id=123,
        display_name="うさぽ",
        level=11,
        minecraft_delivered=True,
        discord_delivered=False,
    )
    bot._level_bot_xp.fetch_level_ups = AsyncMock(  # type: ignore[method-assign]
        return_value=[event]
    )
    ack = AsyncMock(return_value=True)
    bot._level_bot_xp.acknowledge_level_up = ack  # type: ignore[method-assign]
    bot._send = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(bot._sync_minecraft_level_up_announcements())

    assert rcon.commands == []
    ack.assert_awaited_once_with(8, 456, "discord")
    bot._send.assert_awaited_once()  # type: ignore[attr-defined]
