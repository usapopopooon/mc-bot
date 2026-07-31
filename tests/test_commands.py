import asyncio

import discord
from discord import app_commands

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.ui import AdminPanelView, ServerControlView, VoiceControlView


def test_registers_manager_only_configuration_commands() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    group = bot.tree.get_command("mc-config")

    assert isinstance(group, app_commands.Group)
    assert group.guild_only
    assert group.default_permissions == discord.Permissions(manage_guild=True)
    assert {command.name for command in group.commands} == {
        "admin-panel",
        "approval",
        "channel",
        "panel",
        "player-count",
        "show",
    }
    player_count = group.get_command("player-count")
    assert isinstance(player_count, app_commands.Command)
    assert {choice.value for choice in player_count.parameters[0].choices} == {
        "enable",
        "disable",
        "remove",
    }


def test_admin_panel_exposes_server_controls() -> None:
    async def build_views() -> tuple[AdminPanelView, ServerControlView, VoiceControlView]:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        return (
            AdminPanelView(bot),
            ServerControlView(bot, owner_id=123),
            VoiceControlView(bot, owner_id=123),
        )

    admin_panel, controls, voice_controls = asyncio.run(build_views())

    assert {item.label for item in admin_panel.children} >= {
        "Minecraft読み上げ",
        "Whitelist一覧",
        "サーバー操作",
    }
    assert {item.label for item in controls.children} == {
        "Whitelist",
        "キック",
        "サーバー告知",
        "パフォーマンス",
        "プレイヤー",
        "天候・時刻",
        "最新状態",
    }
    assert {
        item.label if isinstance(item, discord.ui.Button) else item.placeholder
        for item in voice_controls.children
    } == {
        "Minecraft読み上げ先VCを選択",
        "テスト読み上げ",
        "切断",
    }
