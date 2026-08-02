import asyncio
from unittest.mock import AsyncMock

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.settings import RuntimeSettings


class ExperienceRcon:
    def __init__(self) -> None:
        self.level = 1
        self.points = 0
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command == "list":
            return "There are 1 of a max of 20 players online: Steve"
        if command == "experience query Steve levels":
            return f"Steve has {self.level} experience levels"
        if command == "experience query Steve points":
            return f"Steve has {self.points} experience points"
        raise AssertionError(f"unexpected RCON command: {command}")


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
