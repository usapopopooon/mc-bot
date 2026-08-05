import asyncio
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from mc_bot.accounts import MinecraftAccount
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.settings import RuntimeSettings
from mc_bot.ui import (
    AdminPanelView,
    RegistrationModal,
    ServerControlView,
    VoiceControlView,
    access_panel_embed,
)


def test_access_panel_explains_edition_specific_names() -> None:
    description = access_panel_embed("automatic").description

    assert description is not None
    assert "Java版のプレイヤー名" in description
    assert "Xboxゲーマータグ" in description
    assert description.count("自分のキャラクターの頭上に表示される名前") == 2
    assert "Switch・Xbox・PlayStation・スマホ・Windows" in description
    assert "Discordの表示名" not in description
    assert "メールアドレス" not in description
    assert "「.」" not in description


def test_registration_modal_uses_edition_specific_labels() -> None:
    async def build_modals() -> tuple[RegistrationModal, RegistrationModal]:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        return RegistrationModal(bot, "java"), RegistrationModal(bot, "bedrock")

    java_modal, bedrock_modal = asyncio.run(build_modals())

    assert java_modal.minecraft_name_label.text == "Java版のプレイヤー名"
    assert bedrock_modal.minecraft_name_label.text == "Xboxゲーマータグ"
    assert "キャラクターの頭上に表示される名前" in (
        java_modal.minecraft_name_label.description or ""
    )
    assert "キャラクターの頭上に表示される名前" in (
        bedrock_modal.minecraft_name_label.description or ""
    )
    assert "." not in (bedrock_modal.minecraft_name.placeholder or "")


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
        "status-panel",
    }
    player_count = group.get_command("player-count")
    assert isinstance(player_count, app_commands.Command)
    assert {choice.value for choice in player_count.parameters[0].choices} == {
        "enable",
        "disable",
        "remove",
    }
    voice = bot.tree.get_command("vc")
    assert isinstance(voice, app_commands.Command)
    assert voice.guild_only
    assert voice.default_permissions is None


def test_vc_command_connects_to_callers_current_voice_channel() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.configure_voice_channel = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.guild.voice_client = None
        interaction.user = Mock(spec=discord.Member)
        interaction.user.voice = Mock()
        interaction.user.voice.channel = Mock(spec=discord.VoiceChannel)

        await bot._voice_command(interaction)

        bot.configure_voice_channel.assert_awaited_once_with(
            interaction, interaction.user.voice.channel
        )

    asyncio.run(exercise())


def test_vc_command_asks_caller_to_join_voice_first() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.configure_voice_channel = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.guild.voice_client = None
        interaction.user = Mock(spec=discord.Member)
        interaction.user.voice = None
        interaction.response.send_message = AsyncMock()

        await bot._voice_command(interaction)

        bot.configure_voice_channel.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "先に接続させたいVCへ参加してから `/vc` を実行してください。",
            ephemeral=True,
        )

    asyncio.run(exercise())


def test_vc_command_disconnects_when_bot_is_connected() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.configure_voice_channel = AsyncMock()  # type: ignore[method-assign]
        bot.disconnect_voice = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.guild.voice_client.is_connected.return_value = True

        await bot._voice_command(interaction)

        bot.disconnect_voice.assert_awaited_once_with(interaction)
        bot.configure_voice_channel.assert_not_awaited()

    asyncio.run(exercise())


def test_empty_voice_channel_auto_disconnects_ignoring_bots() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._settings = RuntimeSettings(voice_channel_id=456, voice_enabled=True)
        bot._save_settings = AsyncMock()  # type: ignore[method-assign]
        voice_client = Mock()
        voice_client.is_connected.return_value = True
        voice_client.disconnect = AsyncMock()
        voice_client.channel.id = 456
        voice_client.channel.members = [Mock(bot=True), Mock(bot=True)]
        member = Mock(spec=discord.Member)
        member.guild.voice_client = voice_client

        await bot.on_voice_state_update(member, Mock(), Mock())

        saved = bot._save_settings.await_args.args[0]
        assert saved.voice_enabled is False
        assert saved.voice_channel_id is None
        voice_client.disconnect.assert_awaited_once_with(force=True)

    asyncio.run(exercise())


def test_voice_channel_stays_connected_while_a_human_remains() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._settings = RuntimeSettings(voice_channel_id=456, voice_enabled=True)
        bot._save_settings = AsyncMock()  # type: ignore[method-assign]
        voice_client = Mock()
        voice_client.is_connected.return_value = True
        voice_client.disconnect = AsyncMock()
        voice_client.channel.id = 456
        voice_client.channel.members = [Mock(bot=True), Mock(bot=False)]
        member = Mock(spec=discord.Member)
        member.guild.voice_client = voice_client

        await bot.on_voice_state_update(member, Mock(), Mock())

        bot._save_settings.assert_not_awaited()
        voice_client.disconnect.assert_not_awaited()

    asyncio.run(exercise())


def test_voice_connection_posts_public_explanation_embed() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                voicevox_tts_api_url="http://tts:8080",
                voicevox_tts_api_token="tts-secret",
            )
        )
        bot._connect_voice_channel = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._save_settings = AsyncMock()  # type: ignore[method-assign]
        bot._voice_player.enqueue = Mock(return_value=True)  # type: ignore[method-assign]
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = 456
        channel.mention = "<#456>"
        channel.guild.id = 123
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 789
        interaction.guild_id = 123
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot.configure_voice_channel(interaction, channel)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        followup = interaction.followup.send.await_args.kwargs
        assert followup["ephemeral"] is False
        mentions = followup["allowed_mentions"]
        assert mentions.everyone is False
        assert mentions.users is False
        assert mentions.roles is False
        assert followup["embed"].title == "🔊 Minecraft読み上げを開始しました"
        assert "チャット・参加・退出・進捗・死亡" in followup["embed"].description
        assert "小夜/SAYO" in followup["embed"].description
        bot._voice_player.enqueue.assert_called_once_with(123, "せつぞくしました")

    asyncio.run(exercise())


def test_discord_identity_uses_server_display_name_for_speech() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._settings = RuntimeSettings(guild_id=123)
        member = Mock(spec=discord.Member)
        member.id = 789
        member.display_name = "サーバー表示名"
        guild = Mock(spec=discord.Guild)
        guild.get_member.return_value = member
        bot.get_guild = Mock(return_value=guild)  # type: ignore[method-assign]
        bot._accounts.update_discord_username = Mock()  # type: ignore[method-assign]
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid=None,
            discord_user_id=789,
            discord_username="old_username",
            managed=True,
            source="self",
            status="active",
            created_by=789,
            approval_message_id=None,
        )

        assert await bot._discord_identity(account) == (789, "サーバー表示名")
        bot._accounts.update_discord_username.assert_called_once_with(789, "サーバー表示名")

    asyncio.run(exercise())


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


def test_server_announcement_is_also_sent_to_discord_log() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret", rcon_password="secret"))
        bot.validate_runtime_admin = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._execute_checked_rcon = AsyncMock(return_value="")  # type: ignore[method-assign]
        bot._send = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.guild_id = 456
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot.announce_server(interaction, "メンテナンスを開始します")

        bot._execute_checked_rcon.assert_awaited_once()
        bot._send.assert_awaited_once()  # type: ignore[attr-defined]
        embed = bot._send.await_args.args[0]  # type: ignore[attr-defined]
        assert embed.description == "📢 **[サーバー告知]** メンテナンスを開始します"
        interaction.followup.send.assert_awaited_once_with(
            "✅ サーバー内へ告知し、チャンネルログにも投稿しました。",
            ephemeral=True,
        )

    asyncio.run(exercise())


def test_server_announcement_reports_discord_log_failure() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret", rcon_password="secret"))
        bot.validate_runtime_admin = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._execute_checked_rcon = AsyncMock(return_value="")  # type: ignore[method-assign]
        bot._send = AsyncMock(side_effect=RuntimeError("channel unavailable"))  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.guild_id = 456
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot.announce_server(interaction, "メンテナンスを開始します")

        bot._execute_checked_rcon.assert_awaited_once()
        interaction.followup.send.assert_awaited_once_with(
            "⚠️ サーバー内へ告知しましたが、チャンネルログへ投稿できませんでした。",
            ephemeral=True,
        )

    asyncio.run(exercise())


def test_failed_minecraft_announcement_is_not_logged_as_success() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret", rcon_password="secret"))
        bot.validate_runtime_admin = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._execute_checked_rcon = AsyncMock(  # type: ignore[method-assign]
            side_effect=ValueError("Minecraftコマンドが失敗しました")
        )
        bot._send = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot.announce_server(interaction, "メンテナンスを開始します")

        bot._send.assert_not_awaited()  # type: ignore[attr-defined]
        interaction.followup.send.assert_awaited_once_with(
            "告知できませんでした: Minecraftコマンドが失敗しました",
            ephemeral=True,
        )

    asyncio.run(exercise())


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


def test_restored_voice_connection_does_not_announce() -> None:
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
        bot._connect_voice_channel = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._voice_player.enqueue = Mock(return_value=True)  # type: ignore[method-assign]

        await bot._restore_voice_connection()
        await bot._restore_voice_connection()

        assert bot._connect_voice_channel.await_count == 2
        bot._voice_player.enqueue.assert_not_called()

    asyncio.run(exercise())
