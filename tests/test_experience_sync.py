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
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("tellraw @a "):
            return ""
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
    assert "VC XPが2倍" in tellraw_commands[0]
    send_log.assert_awaited_once()
    assert send_log.await_args.args[0].description == (
        "🎮🔊 **[うさぽサーバー] Steve (<@123>) さん** が"
        "MinecraftとVCに同時接続しました\n"
        "同時接続中は、サーバーでのVC XPが **2倍** になります!"
    )


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

    assert len(rcon.commands) == 1
    assert rcon.commands[0].startswith("tellraw @a ")
    assert "うさぽサーバー" in rcon.commands[0]
    assert ack.await_args_list[0].args == (7, 456, "minecraft")
    assert ack.await_args_list[1].args == (7, 456, "discord")
    send_log.assert_awaited_once()
    embed = send_log.await_args.args[0]
    assert embed.title is None
    assert embed.description == (
        "🎉 **うさぽ (<@123>) さん** がlevel-botでレベル **10** になりました!"
    )


def test_sync_does_not_repeat_already_delivered_minecraft_message(tmp_path) -> None:
    config = Config(
        discord_token="test",
        accounts_path=tmp_path / "accounts.db",
        rcon_password="test",
        level_bot_api_url="https://levels.example.test",
        level_bot_api_token="xp-secret",
    )
    bot = MinecraftDiscordBot(config)
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

    assert rcon.commands == []
    ack.assert_awaited_once_with(8, 456, "discord")
