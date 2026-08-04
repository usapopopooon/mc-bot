import asyncio
from unittest.mock import AsyncMock, MagicMock

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import MinecraftLevelUpEvent, MinecraftVoiceHeartbeatResult
from mc_bot.settings import RuntimeSettings
from mc_bot.tailer import Cursor, PendingLine


class ExperienceRcon:
    def __init__(self) -> None:
        self.level = 1
        self.points = 0
        self.add_error_response: str | None = None
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("tellraw @a "):
            return ""
        if command.startswith("experience add Steve ") and command.endswith(" points"):
            if self.add_error_response is not None:
                return self.add_error_response
            added = int(command.split()[3])
            self.points += added
            return f"Added {added} experience points to Steve"
        if command == "list":
            return "There are 1 of a max of 20 players online: Steve"
        if command == "experience query Steve levels":
            return f"Steve has {self.level} experience levels"
        if command == "experience query Steve points":
            return f"Steve has {self.points} experience points"
        raise AssertionError(f"unexpected RCON command: {command}")


class OneLineTailer:
    def __init__(self, line: PendingLine) -> None:
        self._line = line
        self.acknowledged: list[PendingLine] = []

    async def lines(self):  # type: ignore[no-untyped-def]
        yield self._line

    def acknowledge(self, line: PendingLine) -> None:
        self.acknowledged.append(line)


def test_sync_baselines_then_delivers_positive_xp_delta(tmp_path) -> None:
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
    send = AsyncMock(return_value=True)
    bot._level_bot_xp.send = send  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, False)
    )

    async def exercise() -> None:
        await bot._sync_minecraft_xp()
        send.assert_not_awaited()

        rcon.points = 5
        await bot._sync_minecraft_xp()

    asyncio.run(exercise())

    send.assert_awaited_once()
    event = send.await_args.args[0]
    assert event.minecraft_xp == 5
    assert event.discord_user_id == 123
    assert event.guild_id == 456
    assert bot._accounts.list_minecraft_xp_outbox() == []


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
        await bot._sync_minecraft_xp()
        await bot._sync_minecraft_xp()
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
    rcon.level = 0
    bot._rcon = rcon  # type: ignore[assignment]
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
        rcon.points = 3
        await bot._sync_minecraft_xp()
        await bot._sync_minecraft_xp()

    asyncio.run(exercise())

    bonus_commands = [
        command for command in rcon.commands if command.startswith("experience add Steve ")
    ]
    assert bonus_commands == ["experience add Steve 3 points"]
    assert rcon.points == 6
    sent_events = bot._level_bot_xp.send.await_args_list  # type: ignore[attr-defined]
    assert len(sent_events) == 1
    assert sent_events[0].args[0].minecraft_xp == 3


def test_voice_bonus_activation_baselines_prior_xp_before_doubling(tmp_path) -> None:
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
    rcon.level = 0
    bot._rcon = rcon  # type: ignore[assignment]
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    rcon.points = 3
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
        assert not any(command.startswith("experience add Steve ") for command in rcon.commands)
        rcon.points = 5
        await bot._sync_minecraft_xp()

    asyncio.run(exercise())

    bonus_commands = [
        command for command in rcon.commands if command.startswith("experience add Steve ")
    ]
    assert bonus_commands == ["experience add Steve 2 points"]
    assert rcon.points == 7


def test_voice_bonus_deactivation_settles_xp_earned_while_active(tmp_path) -> None:
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
    rcon.level = 0
    rcon.points = 3
    bot._rcon = rcon  # type: ignore[assignment]
    bot._voice_bonus_active_users.add(123)
    bot._accounts.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, False)
    )

    asyncio.run(bot._sync_voice_bonus_for_account(account))

    assert rcon.points == 6
    assert 123 not in bot._voice_bonus_active_users


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
        "達成したので、サーバーでの **100 XP**を獲得しました!"
    )
    assert rcon.commands[0].startswith("tellraw @a ")
    assert "うさぽサーバー" in rcon.commands[0]
    assert "100 XP" in rcon.commands[0]
    assert tailer.acknowledged == [line]
    assert bot._accounts.list_minecraft_xp_outbox() == []


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


def test_sync_pauses_observation_while_api_outbox_is_blocked(tmp_path) -> None:
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
    send = AsyncMock(return_value=False)
    bot._level_bot_xp.send = send  # type: ignore[method-assign]
    bot._level_bot_xp.send_voice_heartbeat = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftVoiceHeartbeatResult(0, False)
    )

    async def exercise() -> int:
        await bot._sync_minecraft_xp()
        rcon.points = 5
        await bot._sync_minecraft_xp()
        commands_after_failure = len(rcon.commands)
        await bot._sync_minecraft_xp()
        return commands_after_failure

    commands_after_failure = asyncio.run(exercise())

    assert bot._accounts.list_minecraft_xp_outbox()
    assert len(rcon.commands) == commands_after_failure


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

    assert len(rcon.commands) == 2
    assert rcon.commands[0] == "list"
    assert rcon.commands[1].startswith("tellraw @a ")
    assert "うさぽサーバー" in rcon.commands[1]
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

    assert rcon.commands == ["list"]
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

    assert rcon.commands == ["list"]
    ack.assert_awaited_once_with(8, 456, "discord")
    bot._send.assert_awaited_once()  # type: ignore[attr-defined]
