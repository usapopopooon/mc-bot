import discord
from discord import app_commands

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config


def test_registers_manager_only_configuration_commands() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    group = bot.tree.get_command("mc-config")

    assert isinstance(group, app_commands.Group)
    assert group.guild_only
    assert group.default_permissions == discord.Permissions(manage_guild=True)
    assert {command.name for command in group.commands} == {"channel", "show"}
