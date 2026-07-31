import asyncio
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.settings import RuntimeSettings
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
        "読み上げ確認",
        "切断",
    }


def test_voice_check_uses_operational_status_message() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._settings = RuntimeSettings(voice_enabled=True)
        bot._voice_player.is_connected = Mock(return_value=True)  # type: ignore[method-assign]
        bot._voice_player.enqueue = Mock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.guild_id = 123
        interaction.response.send_message = AsyncMock()

        await bot.test_voice(interaction)

        bot._voice_player.enqueue.assert_called_once_with(
            123, "マインクラフトの読み上げは正常に動作しています"
        )
        interaction.response.send_message.assert_awaited_once_with(
            "読み上げ確認音声をキューへ追加しました。", ephemeral=True
        )

    asyncio.run(exercise())


def test_restored_voice_connection_announces_once() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                voicevox_tts_api_url="http://tts:8080",
                voicevox_tts_api_token="tts-secret",
            )
        )
        bot._settings = RuntimeSettings(voice_channel_id=456, voice_enabled=True)
        channel = Mock(spec=discord.VoiceChannel)
        channel.guild.id = 123
        bot.get_channel = Mock(return_value=channel)  # type: ignore[method-assign]
        bot._connect_voice_channel = AsyncMock(  # type: ignore[method-assign]
            side_effect=[True, False]
        )
        bot._voice_player.enqueue = Mock(return_value=True)  # type: ignore[method-assign]

        await bot._restore_voice_connection()
        await bot._restore_voice_connection()

        assert bot._connect_voice_channel.await_count == 2
        bot._voice_player.enqueue.assert_called_once_with(123, "せつぞくしました")

    asyncio.run(exercise())
