import asyncio
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands

from mc_bot.accounts import WHITELIST_RETRY_LIMIT, AccountStore, MinecraftAccount
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.settings import RuntimeSettings
from mc_bot.ui import (
    AccountSelect,
    AccountSelectView,
    AdminPanelView,
    ConfirmMinecraftIdCorrectionView,
    ConfirmRegistrationView,
    ConfirmRelinkView,
    ConfirmRemovalView,
    MinecraftIdCorrectionModal,
    RegistrationModal,
    ServerControlView,
    TargetUserSelect,
    VoiceControlView,
    access_panel_embed,
)

CORRECT_JAVA_UUID = "ec561538-f3fd-461d-aff5-086b22154bce"
CORRECT_BEDROCK_UUID = "00000000-0000-0000-0009-01fb7be05000"


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


@pytest.mark.parametrize(
    "entered_name",
    [
        "yuki1991#1261",
        "yuki1991 #1261",
        "yuki1991\uff031261",
        "yuki1991\uff03\uff11\uff12\uff16\uff11",
    ],
)
def test_normalizes_modern_bedrock_gamertag_to_minecraft_name(entered_name: str) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    assert bot._normalize_player_name("bedrock", entered_name) == (
        "yuki19911261",
        ".yuki19911261",
    )


@pytest.mark.parametrize("entered_name", ["yuki1991#", "yuki1991#abc", "a#b#1234"])
def test_rejects_invalid_bedrock_gamertag_suffix(entered_name: str) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    with pytest.raises(ValueError, match="末尾の数字サフィックス"):
        bot._normalize_player_name("bedrock", entered_name)


def test_java_name_still_rejects_hash_suffix() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    with pytest.raises(ValueError, match="Java版の名前"):
        bot._normalize_player_name("java", "yuki1991#1261")


def test_keeps_already_classic_bedrock_gamertag_unchanged() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    assert bot._normalize_player_name("bedrock", "yuki19911261") == (
        "yuki19911261",
        ".yuki19911261",
    )


def test_account_line_shows_when_automatic_whitelist_retries_stopped() -> None:
    account = MinecraftAccount(
        id=1,
        edition="bedrock",
        minecraft_name="Missing",
        server_player_name=".Missing",
        player_uuid=None,
        discord_user_id=123,
        discord_username="user",
        managed=True,
        source="self",
        status="pending_add",
        created_by=123,
        approval_message_id=None,
        whitelist_retry_count=WHITELIST_RETRY_LIMIT,
        whitelist_last_error="Minecraft IDが存在しません",
    )

    assert "追加失敗\uff08自動再試行停止\uff09" in MinecraftDiscordBot._account_line(account)


def test_user_can_select_exhausted_whitelist_removal_for_manual_retry() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        account = MinecraftAccount(
            id=1,
            edition="bedrock",
            minecraft_name="OldName",
            server_player_name=".OldName",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="self",
            status="pending_remove",
            created_by=123,
            approval_message_id=None,
            whitelist_retry_count=WHITELIST_RETRY_LIMIT,
            whitelist_last_error="remove failed",
        )
        bot._accounts = Mock()
        bot._accounts.list_for_discord_user.return_value = [account]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.response.send_message = AsyncMock()

        await bot.show_user_accounts(interaction)

        response = interaction.response.send_message.await_args.kwargs
        assert response["ephemeral"] is True
        assert isinstance(response["view"], AccountSelectView)
        select = response["view"].children[0]
        assert isinstance(select, AccountSelect)
        assert select.options[0].description == "Bedrock版 / Whitelist解除を再試行"

        select._values = ["1"]  # type: ignore[attr-defined]
        interaction.response.edit_message = AsyncMock()
        await select.callback(interaction)

        confirmation = interaction.response.edit_message.await_args.kwargs
        assert "Whitelist解除を再試行" in confirmation["content"]
        assert isinstance(confirmation["view"], ConfirmRemovalView)
        assert confirmation["view"].confirm.label == "解除を再試行"

    asyncio.run(exercise())


def test_unlinked_account_selection_stops_when_uuid_import_fails() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._accounts = Mock()
        bot._import_whitelist = AsyncMock(return_value=False)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.response.send_message = AsyncMock()

        await bot.show_unlinked_accounts(interaction)

        bot._accounts.list_unlinked.assert_not_called()
        response = interaction.response.send_message.await_args
        assert "紐付け操作を停止" in response.args[0]
        assert response.kwargs["ephemeral"] is True

    asyncio.run(exercise())


def test_account_owner_can_manually_retry_exhausted_whitelist_removal() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="OldName",
            server_player_name="OldName",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="self",
            status="pending_remove",
            created_by=123,
            approval_message_id=None,
            whitelist_retry_count=WHITELIST_RETRY_LIMIT,
            whitelist_last_error="remove failed",
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = account
        bot._remove_from_whitelist = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock(spec=discord.Member)
        interaction.user.id = 123
        interaction.user.guild_permissions.manage_guild = False
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.remove_account(interaction, account.id)

        interaction.response.defer.assert_awaited_once_with()
        bot._remove_from_whitelist.assert_awaited_once_with(account)
        response = interaction.edit_original_response.await_args.kwargs
        assert "Whitelist解除を再試行し、完了" in response["content"]
        assert response["view"] is None

    asyncio.run(exercise())


def test_failed_manual_removal_retry_starts_a_new_bounded_retry_cycle() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="OldName",
            server_player_name="OldName",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="self",
            status="pending_remove",
            created_by=123,
            approval_message_id=None,
            whitelist_retry_count=WHITELIST_RETRY_LIMIT,
            whitelist_last_error="remove failed",
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = account
        bot._remove_from_whitelist = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("still unavailable")
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.user = Mock(spec=discord.Member)
        interaction.user.id = 123
        interaction.user.guild_permissions.manage_guild = False
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.remove_account(interaction, account.id)

        bot._accounts.update_status.assert_called_once_with(account.id, "pending_remove")
        response = interaction.edit_original_response.await_args.kwargs
        assert "解除を再試行しましたが、反映できませんでした" in response["content"]
        assert f"最大{WHITELIST_RETRY_LIMIT}回" in response["content"]

    asyncio.run(exercise())


@pytest.mark.parametrize("source", ["self", "admin"])
def test_registration_confirmation_shows_normalized_bedrock_name(source: str) -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        target = Mock(spec=discord.Member)
        target.id = 123
        target.mention = "<@123>"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.response.send_message = AsyncMock()

        await bot.confirm_registration(
            interaction,
            edition="bedrock",
            minecraft_name="yuki1991#1261",
            target=target,
            source=source,
        )

        content = interaction.response.send_message.await_args.args[0]
        response = interaction.response.send_message.await_args.kwargs
        assert "yuki19911261" in content
        assert "#1261" not in content
        assert isinstance(response["view"], ConfirmRegistrationView)
        assert response["view"].minecraft_name == "yuki19911261"

    asyncio.run(exercise())


@pytest.mark.parametrize("source", ["self", "admin"])
def test_registration_commit_stores_only_normalized_bedrock_name(source: str) -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        account = MinecraftAccount(
            id=1,
            edition="bedrock",
            minecraft_name="yuki19911261",
            server_player_name=".yuki19911261",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source=source,
            status="pending_add",
            created_by=123,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.create_registration.return_value = account
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                ".yuki19911261",
                "00000000-0000-0000-0009-01fb7be05000",
            )
        )
        bot._add_to_whitelist = AsyncMock()  # type: ignore[method-assign]
        target = Mock(spec=discord.Member)
        target.id = 123
        target.display_name = "user"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.followup.send = AsyncMock()

        await bot.register_account(
            interaction,
            edition="bedrock",
            minecraft_name="yuki1991\uff03\uff11\uff12\uff16\uff11",
            target=target,
            source=source,
        )

        create_args = bot._accounts.create_registration.call_args.kwargs
        assert create_args["minecraft_name"] == "yuki19911261"
        assert create_args["server_player_name"] == ".yuki19911261"
        assert create_args["player_uuid"] == "00000000-0000-0000-0009-01fb7be05000"
        bot._add_to_whitelist.assert_awaited_once_with(account)
        content = interaction.followup.send.await_args.args[0]
        assert "yuki19911261" in content
        assert "#" not in content
        assert "\uff03" not in content

    asyncio.run(exercise())


def test_registration_does_not_store_name_when_uuid_cannot_be_resolved() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._accounts = Mock()
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            side_effect=ValueError("Bedrock版アカウント Missing が存在しません")
        )
        bot._add_to_whitelist = AsyncMock()  # type: ignore[method-assign]
        target = Mock(spec=discord.Member)
        target.id = 123
        target.display_name = "user"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.followup.send = AsyncMock()

        await bot.register_account(
            interaction,
            edition="bedrock",
            minecraft_name="Missing",
            target=target,
            source="self",
        )

        bot._accounts.create_registration.assert_not_called()
        bot._add_to_whitelist.assert_not_awaited()
        message = interaction.followup.send.await_args.args[0]
        assert "確認できないため登録しませんでした" in message
        assert "存在しません" in message

    asyncio.run(exercise())


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
        "item-gacha-panel",
        "market-channel",
        "market-log-channel",
        "quest-channel",
        "quest-log-channel",
        "panel",
        "player-count",
        "resource-panel",
        "show",
        "status-panel",
        "xp-panel",
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


def test_configures_specific_market_log_channel(tmp_path) -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(
            Config(discord_token="secret", settings_path=tmp_path / "settings.json")
        )
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        target = Mock(spec=discord.TextChannel)
        target.id = 777
        target.guild = Mock()
        target.guild.id = 1001
        target.mention = "<#777>"
        bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
            return_value=target
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.channel = target
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot._configure_market_log_channel(interaction)

        assert bot._settings.guild_id == 1001
        assert bot._settings.market_log_channel_id == 777
        assert bot._settings_store.load() == bot._settings
        bot._resolve_and_validate_channel.assert_awaited_once_with(  # type: ignore[attr-defined]
            777,
            require_embeds=True,
        )
        response = interaction.followup.send.await_args
        assert "フリマ成約ログ" in response.args[0]
        assert response.kwargs["ephemeral"] is True

    asyncio.run(exercise())


def test_configures_specific_quest_log_channel_and_flushes_pending_logs(tmp_path) -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(
            Config(discord_token="secret", settings_path=tmp_path / "settings.json")
        )
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._deliver_quest_logs = AsyncMock()  # type: ignore[method-assign]
        target = Mock(spec=discord.TextChannel)
        target.id = 778
        target.guild = Mock()
        target.guild.id = 1001
        target.mention = "<#778>"
        bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
            return_value=target
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.channel = target
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot._configure_quest_log_channel(interaction)

        assert bot._settings.guild_id == 1001
        assert bot._settings.quest_log_channel_id == 778
        assert bot._settings_store.load() == bot._settings
        bot._resolve_and_validate_channel.assert_awaited_once_with(  # type: ignore[attr-defined]
            778,
            require_embeds=True,
            require_message_history=True,
        )
        bot._deliver_quest_logs.assert_awaited_once()  # type: ignore[attr-defined]
        response = interaction.followup.send.await_args
        assert "クエスト完了ログ" in response.args[0]
        assert response.kwargs["ephemeral"] is True

    asyncio.run(exercise())


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


@pytest.mark.parametrize(
    ("members", "should_disconnect"),
    [
        ([], True),
        ([True], True),
        ([True, False], False),
        ([True, False, False], False),
        ([True, False, False, False, False], False),
    ],
)
def test_voice_auto_disconnect_participant_boundaries(
    members: list[bool],
    should_disconnect: bool,
) -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._settings = RuntimeSettings(voice_channel_id=456, voice_enabled=True)
        bot._save_settings = AsyncMock()  # type: ignore[method-assign]
        voice_client = Mock()
        voice_client.is_connected.return_value = True
        voice_client.disconnect = AsyncMock()
        voice_client.channel.id = 456
        voice_client.channel.members = [Mock(bot=is_bot) for is_bot in members]
        member = Mock(spec=discord.Member)
        member.guild.voice_client = voice_client

        await bot.on_voice_state_update(member, Mock(), Mock())

        if should_disconnect:
            voice_client.disconnect.assert_awaited_once_with(force=True)
        else:
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
        "Minecraft IDを修正",
        "Discord紐付け先を修正",
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


def test_relink_selects_account_then_new_discord_user() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.confirm_account_relink = AsyncMock()  # type: ignore[method-assign]
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="uuid-1",
            discord_user_id=123,
            discord_username="wrong-user",
            managed=True,
            source="admin",
            status="active",
            created_by=999,
            approval_message_id=None,
        )
        account_select = AccountSelect(bot, [account], "relink")
        account_select._values = ["1"]  # type: ignore[attr-defined]
        interaction = Mock(spec=discord.Interaction)
        interaction.response.edit_message = AsyncMock()

        await account_select.callback(interaction)

        account_response = interaction.response.edit_message.await_args.kwargs
        target_select = account_response["view"].children[0]
        assert isinstance(target_select, TargetUserSelect)
        assert target_select.purpose == "relink"
        assert target_select.account_id == 1

        target = Mock(spec=discord.Member)
        target.id = 456
        target.bot = False
        target_select._values = [target]  # type: ignore[attr-defined]
        await target_select.callback(interaction)

        bot.confirm_account_relink.assert_awaited_once_with(interaction, 1, target)

    asyncio.run(exercise())


def test_minecraft_id_correction_select_opens_name_modal() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        account = MinecraftAccount(
            id=1,
            edition="bedrock",
            minecraft_name="WrongName",
            server_player_name=".WrongName",
            player_uuid="uuid-1",
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="admin",
            status="pending_remove",
            created_by=999,
            approval_message_id=None,
        )
        select = AccountSelect(bot, [account], "correct_id")
        select._values = ["1"]  # type: ignore[attr-defined]
        interaction = Mock(spec=discord.Interaction)
        interaction.response.send_modal = AsyncMock()

        await select.callback(interaction)

        modal = interaction.response.send_modal.await_args.args[0]
        assert isinstance(modal, MinecraftIdCorrectionModal)
        assert modal.account_id == 1
        assert modal.correct_name_label.text == "正しいXboxゲーマータグ"

    asyncio.run(exercise())


def test_show_relinkable_accounts_passes_store_results_to_relink_view() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="uuid-1",
            discord_user_id=123,
            discord_username="wrong-user",
            managed=True,
            source="admin",
            status="active",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.list_relinkable.return_value = [account]
        interaction = Mock(spec=discord.Interaction)
        interaction.response.send_message = AsyncMock()

        await bot.show_relinkable_accounts(interaction)

        bot._accounts.list_relinkable.assert_called_once_with()
        response = interaction.response.send_message.await_args.kwargs
        assert response["ephemeral"] is True
        assert isinstance(response["view"], AccountSelectView)
        select = response["view"].children[0]
        assert isinstance(select, AccountSelect)
        assert select.purpose == "relink"
        assert [(option.value, option.label) for option in select.options] == [("1", "Steve")]
        assert select.options[0].description == "Java版 / 現在: wrong-user"

    asyncio.run(exercise())


def test_show_pending_removal_corrections_uses_separate_direction() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="WrongName",
            server_player_name="WrongName",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="admin",
            status="pending_remove",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.list_pending_removal_corrections.return_value = [account]
        interaction = Mock(spec=discord.Interaction)
        interaction.response.send_message = AsyncMock()

        await bot.show_pending_removal_corrections(interaction)

        bot._accounts.list_pending_removal_corrections.assert_called_once_with()
        response = interaction.response.send_message.await_args.kwargs
        assert "誤IDの削除は取り消さず" in interaction.response.send_message.await_args.args[0]
        select = response["view"].children[0]
        assert isinstance(select, AccountSelect)
        assert select.purpose == "correct_id"

    asyncio.run(exercise())


def test_reassign_account_link_preserves_whitelist_management() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        bot._add_to_whitelist = AsyncMock()  # type: ignore[method-assign]
        changed = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="uuid-1",
            discord_user_id=456,
            discord_username="correct-user",
            managed=True,
            source="admin",
            status="active",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.reassign_discord_user.return_value = changed
        target = Mock(spec=discord.Member)
        target.id = 456
        target.display_name = "correct-user"
        target.mention = "<@456>"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild_id = 1001
        interaction.response.edit_message = AsyncMock()

        await bot.reassign_account_link(
            interaction,
            account_id=1,
            expected_discord_user_id=123,
            target=target,
        )

        bot._accounts.reassign_discord_user.assert_called_once_with(
            1,
            expected_discord_user_id=123,
            discord_user_id=456,
            discord_username="correct-user",
        )
        bot._add_to_whitelist.assert_not_awaited()
        response = interaction.response.edit_message.await_args.kwargs
        assert "Whitelistと管理方法は変更していません" in response["content"]
        bot._audit_server_action.assert_called_once()

    asyncio.run(exercise())


def test_confirm_account_relink_displays_old_and_new_owner() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="uuid-1",
            discord_user_id=123,
            discord_username="wrong-user",
            managed=False,
            source="legacy",
            status="active",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = account
        target = Mock(spec=discord.Member)
        target.id = 456
        target.mention = "<@456>"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.response.edit_message = AsyncMock()

        await bot.confirm_account_relink(interaction, 1, target)

        response = interaction.response.edit_message.await_args.kwargs
        assert "現在: <@123>" in response["content"]
        assert "変更後: <@456>" in response["content"]
        assert "送信待ちのXPは移動せず" in response["content"]
        assert isinstance(response["view"], ConfirmRelinkView)
        assert response["view"].expected_discord_user_id == 123

    asyncio.run(exercise())


def test_confirm_account_relink_explains_pending_removal_recovery() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="uuid-1",
            discord_user_id=123,
            discord_username="wrong-user",
            managed=True,
            source="admin",
            status="pending_remove",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = account
        target = Mock(spec=discord.Member)
        target.id = 456
        target.mention = "<@456>"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.response.edit_message = AsyncMock()

        await bot.confirm_account_relink(interaction, 1, target)

        response = interaction.response.edit_message.await_args.kwargs
        assert "削除反映待ちを取り消し" in response["content"]
        assert "削除済みなら再追加" in response["content"]
        assert isinstance(response["view"], ConfirmRelinkView)
        assert response["view"].recover_pending_remove is True

    asyncio.run(exercise())


def test_confirm_account_relink_allows_explicit_missing_owner_recovery() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid=CORRECT_JAVA_UUID,
            discord_user_id=123,
            discord_username="old-user",
            managed=True,
            source="admin",
            status="missing",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = account
        target = Mock(spec=discord.Member)
        target.id = 456
        target.mention = "<@456>"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.response.edit_message = AsyncMock()

        await bot.confirm_account_relink(interaction, 1, target)

        response = interaction.response.edit_message.await_args.kwargs
        assert "削除済み" in response["content"]
        assert "Whitelistへ再追加" in response["content"]
        assert isinstance(response["view"], ConfirmRelinkView)
        assert response["view"].recover_pending_remove is True

    asyncio.run(exercise())


def test_reassign_account_link_recovers_pending_removal() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        changed = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="uuid-1",
            discord_user_id=456,
            discord_username="correct-user",
            managed=True,
            source="admin",
            status="pending_add",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.reassign_discord_user.return_value = changed
        bot._add_to_whitelist_locked = AsyncMock()  # type: ignore[method-assign]
        target = Mock(spec=discord.Member)
        target.id = 456
        target.display_name = "correct-user"
        target.mention = "<@456>"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild_id = 1001
        interaction.response.edit_message = AsyncMock()

        await bot.reassign_account_link(
            interaction,
            account_id=1,
            expected_discord_user_id=123,
            target=target,
            recover_pending_remove=True,
        )

        bot._accounts.reassign_discord_user.assert_called_once_with(
            1,
            expected_discord_user_id=123,
            discord_user_id=456,
            discord_username="correct-user",
            recover_pending_remove=True,
        )
        bot._add_to_whitelist_locked.assert_awaited_once_with(changed)
        response = interaction.response.edit_message.await_args.kwargs
        assert "削除予約を取り消し" in response["content"]
        assert "参加状態を復旧" in response["content"]

    asyncio.run(exercise())


def test_reassign_account_link_keeps_recovery_pending_when_whitelist_add_fails() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        changed = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="uuid-1",
            discord_user_id=456,
            discord_username="correct-user",
            managed=True,
            source="admin",
            status="pending_add",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.reassign_discord_user.return_value = changed
        bot._add_to_whitelist_locked = AsyncMock(side_effect=ValueError("RCON error"))  # type: ignore[method-assign]
        target = Mock(spec=discord.Member)
        target.id = 456
        target.display_name = "correct-user"
        target.mention = "<@456>"
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild_id = 1001
        interaction.response.edit_message = AsyncMock()

        await bot.reassign_account_link(
            interaction,
            account_id=1,
            expected_discord_user_id=123,
            target=target,
            recover_pending_remove=True,
        )

        response = interaction.response.edit_message.await_args.kwargs
        assert "紐付け先を <@456> へ変更" in response["content"]
        assert "Whitelistは再反映待ち" in response["content"]
        assert "Botが後から再試行" in response["content"]

    asyncio.run(exercise())


def test_confirm_minecraft_id_correction_keeps_wrong_id_deletion() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        old_account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="WrongName",
            server_player_name="WrongName",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="admin",
            status="pending_remove",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = old_account
        bot._accounts.get_by_server_player_name.return_value = None
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.response.send_message = AsyncMock()

        await bot.confirm_minecraft_id_correction(interaction, 1, "CorrectName")

        response = interaction.response.send_message.await_args.kwargs
        content = interaction.response.send_message.await_args.args[0]
        assert "誤登録・削除継続: **WrongName**" in content
        assert "正しいID: **CorrectName**" in content
        assert "削除反映待ちは取り消しません" in content
        assert isinstance(response["view"], ConfirmMinecraftIdCorrectionView)
        assert response["view"].expected_discord_user_id == 123

    asyncio.run(exercise())


def test_minecraft_id_correction_normalizes_modern_bedrock_gamertag() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        old_account = MinecraftAccount(
            id=1,
            edition="bedrock",
            minecraft_name="WrongName",
            server_player_name=".WrongName",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="admin",
            status="pending_remove",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = old_account
        bot._accounts.get_by_server_player_name.return_value = None
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.response.send_message = AsyncMock()

        await bot.confirm_minecraft_id_correction(interaction, 1, "yuki1991#1261")

        bot._accounts.get_by_server_player_name.assert_called_once_with(".yuki19911261")
        response = interaction.response.send_message.await_args.kwargs
        assert isinstance(response["view"], ConfirmMinecraftIdCorrectionView)
        assert response["view"].minecraft_name == "yuki19911261"

    asyncio.run(exercise())


def test_confirm_minecraft_id_correction_rejects_id_linked_to_another_user() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        old_account = MinecraftAccount(
            id=1,
            edition="java",
            minecraft_name="WrongName",
            server_player_name="WrongName",
            player_uuid=None,
            discord_user_id=123,
            discord_username="user",
            managed=True,
            source="admin",
            status="pending_remove",
            created_by=999,
            approval_message_id=None,
        )
        used_account = MinecraftAccount(
            id=2,
            edition="java",
            minecraft_name="CorrectName",
            server_player_name="CorrectName",
            player_uuid=None,
            discord_user_id=456,
            discord_username="other-user",
            managed=True,
            source="admin",
            status="active",
            created_by=999,
            approval_message_id=None,
        )
        bot._accounts = Mock()
        bot._accounts.get.return_value = old_account
        bot._accounts.get_by_server_player_name.return_value = used_account
        interaction = Mock(spec=discord.Interaction)
        interaction.response.send_message = AsyncMock()

        await bot.confirm_minecraft_id_correction(interaction, 1, "CorrectName")

        content = interaction.response.send_message.await_args.args[0]
        assert "別のDiscordユーザーに紐付いています" in content
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True

    asyncio.run(exercise())


def test_correct_minecraft_id_creates_new_registration_without_restoring_wrong_id(
    tmp_path,
) -> None:
    async def exercise() -> None:
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        old_account = store.create_registration(
            edition="java",
            minecraft_name="WrongName",
            server_player_name="WrongName",
            discord_user_id=123,
            discord_username="user",
            source="admin",
            status="active",
            created_by=999,
        )
        store.update_status(old_account.id, "pending_remove")
        bot = MinecraftDiscordBot(Config(discord_token="secret", accounts_path=accounts_path))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=("CorrectName", CORRECT_JAVA_UUID)
        )

        async def mark_added(account: MinecraftAccount) -> None:
            store.update_status(account.id, "active")

        bot._add_to_whitelist = AsyncMock(side_effect=mark_added)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild = None
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.correct_pending_removal_minecraft_id(
            interaction,
            old_account_id=old_account.id,
            expected_discord_user_id=123,
            edition="java",
            minecraft_name="CorrectName",
        )

        unchanged_old = store.get(old_account.id)
        corrected = store.get_by_server_player_name("correctname")
        assert unchanged_old is not None
        assert unchanged_old.status == "pending_remove"
        assert unchanged_old.minecraft_name == "WrongName"
        assert corrected is not None
        assert corrected.discord_user_id == 123
        assert corrected.status == "active"
        bot._add_to_whitelist.assert_awaited_once()
        added_account = bot._add_to_whitelist.await_args.args[0]
        assert added_account.id == corrected.id
        assert added_account.minecraft_name == "CorrectName"
        assert added_account.id != unchanged_old.id
        interaction.response.edit_message.assert_awaited_once_with(
            content="⏳ Minecraft IDを確認しています…",
            view=None,
        )
        content = interaction.edit_original_response.await_args.kwargs["content"]
        assert "正しいMinecraft ID **CorrectName**" in content
        assert "削除反映待ちはそのまま継続" in content

    asyncio.run(exercise())


def test_correct_minecraft_id_commit_stores_normalized_bedrock_name(tmp_path) -> None:
    async def exercise() -> None:
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        old_account = store.create_registration(
            edition="bedrock",
            minecraft_name="WrongName",
            server_player_name=".WrongName",
            discord_user_id=123,
            discord_username="user",
            source="admin",
            status="active",
            created_by=999,
        )
        store.update_status(old_account.id, "missing")
        bot = MinecraftDiscordBot(Config(discord_token="secret", accounts_path=accounts_path))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=(".yuki19911261", CORRECT_BEDROCK_UUID)
        )

        async def mark_added(account: MinecraftAccount) -> None:
            store.update_status(account.id, "active")

        bot._add_to_whitelist = AsyncMock(side_effect=mark_added)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild = None
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.correct_pending_removal_minecraft_id(
            interaction,
            old_account_id=old_account.id,
            expected_discord_user_id=123,
            edition="bedrock",
            minecraft_name="yuki1991#1261",
        )

        corrected = store.get_by_server_player_name(".yuki19911261")
        assert corrected is not None
        assert corrected.minecraft_name == "yuki19911261"
        assert corrected.status == "active"
        added_account = bot._add_to_whitelist.await_args.args[0]
        assert added_account.server_player_name == ".yuki19911261"
        interaction.response.edit_message.assert_awaited_once_with(
            content="⏳ Minecraft IDを確認しています…",
            view=None,
        )
        content = interaction.edit_original_response.await_args.kwargs["content"]
        assert "yuki19911261" in content
        assert "#1261" not in content

    asyncio.run(exercise())


def test_correct_minecraft_id_updates_name_in_place_when_uuid_is_unchanged(tmp_path) -> None:
    async def exercise() -> None:
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        old_account = store.create_registration(
            edition="bedrock",
            minecraft_name="BuckedAtol84031",
            server_player_name=".BuckedAtol84031",
            player_uuid=CORRECT_BEDROCK_UUID,
            discord_user_id=123,
            discord_username="user",
            source="admin",
            status="active",
            created_by=999,
        )
        store.update_status(old_account.id, "pending_remove")
        bot = MinecraftDiscordBot(Config(discord_token="secret", accounts_path=accounts_path))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=(".yuki19911261", CORRECT_BEDROCK_UUID)
        )

        async def mark_added(account: MinecraftAccount) -> None:
            store.update_status(account.id, "active")

        bot._add_to_whitelist = AsyncMock(side_effect=mark_added)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild = None
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.correct_pending_removal_minecraft_id(
            interaction,
            old_account_id=old_account.id,
            expected_discord_user_id=123,
            edition="bedrock",
            minecraft_name="yuki1991#1261",
        )

        changed = store.get(old_account.id)
        assert changed is not None
        assert changed.status == "active"
        assert changed.minecraft_name == "yuki19911261"
        assert changed.server_player_name == ".yuki19911261"
        assert changed.player_uuid == CORRECT_BEDROCK_UUID
        assert len(store.list_whitelist_registrations()) == 1
        bot._add_to_whitelist.assert_awaited_once()
        assert "UUIDが同一" in interaction.edit_original_response.await_args.kwargs["content"]

    asyncio.run(exercise())


def test_same_uuid_correction_failure_says_removal_was_cancelled(tmp_path) -> None:
    async def exercise() -> None:
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        old_account = store.create_registration(
            edition="bedrock",
            minecraft_name="OldName",
            server_player_name=".OldName",
            player_uuid=CORRECT_BEDROCK_UUID,
            discord_user_id=123,
            discord_username="user",
            source="admin",
            status="active",
            created_by=999,
        )
        store.update_status(old_account.id, "pending_remove")
        bot = MinecraftDiscordBot(Config(discord_token="secret", accounts_path=accounts_path))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=(".CurrentName", CORRECT_BEDROCK_UUID)
        )
        bot._add_to_whitelist = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("temporary failure")
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild = None
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.correct_pending_removal_minecraft_id(
            interaction,
            old_account_id=old_account.id,
            expected_discord_user_id=123,
            edition="bedrock",
            minecraft_name="CurrentName",
        )

        changed = store.get(old_account.id)
        assert changed is not None
        assert changed.status == "pending_add"
        content = interaction.edit_original_response.await_args.kwargs["content"]
        assert "削除待ちは取り消し" in content
        assert "削除はそのまま継続" not in content

    asyncio.run(exercise())


def test_correct_minecraft_id_keeps_both_actions_pending_when_add_fails(tmp_path) -> None:
    async def exercise() -> None:
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        old_account = store.create_registration(
            edition="java",
            minecraft_name="WrongName",
            server_player_name="WrongName",
            discord_user_id=123,
            discord_username="user",
            source="admin",
            status="active",
            created_by=999,
        )
        store.update_status(old_account.id, "pending_remove")
        bot = MinecraftDiscordBot(Config(discord_token="secret", accounts_path=accounts_path))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=("CorrectName", CORRECT_JAVA_UUID)
        )

        async def reject_missing_id(_: MinecraftAccount) -> None:
            interaction.response.edit_message.assert_awaited_once_with(
                content="⏳ Minecraft IDを確認しています…",
                view=None,
            )
            raise ValueError("Minecraft IDが存在しません")

        bot._add_to_whitelist = AsyncMock(  # type: ignore[method-assign]
            side_effect=reject_missing_id
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild = None
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.correct_pending_removal_minecraft_id(
            interaction,
            old_account_id=old_account.id,
            expected_discord_user_id=123,
            edition="java",
            minecraft_name="CorrectName",
        )

        corrected = store.get_by_server_player_name("CorrectName")
        assert corrected is not None
        assert corrected.status == "pending_add"
        assert store.get(old_account.id).status == "pending_remove"  # type: ignore[union-attr]
        content = interaction.edit_original_response.await_args.kwargs["content"]
        assert "Minecraft IDが存在しません" in content
        assert "Whitelistへの追加は反映待ち" in content
        assert "誤登録の削除はそのまま継続" in content

    asyncio.run(exercise())


def test_correct_minecraft_id_links_existing_unlinked_whitelist(tmp_path) -> None:
    async def exercise() -> None:
        whitelist_path = tmp_path / "whitelist.json"
        whitelist_path.write_text(f'[{{"uuid":"{CORRECT_JAVA_UUID}","name":"CorrectName"}}]')
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        store.import_whitelist(whitelist_path)
        old_account = store.create_registration(
            edition="java",
            minecraft_name="WrongName",
            server_player_name="WrongName",
            discord_user_id=123,
            discord_username="user",
            source="admin",
            status="active",
            created_by=999,
        )
        store.update_status(old_account.id, "pending_remove")
        bot = MinecraftDiscordBot(Config(discord_token="secret", accounts_path=accounts_path))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        bot._resolve_player_profile = AsyncMock(  # type: ignore[method-assign]
            return_value=("CorrectName", CORRECT_JAVA_UUID)
        )
        bot._add_to_whitelist = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 999
        interaction.guild = None
        interaction.response.edit_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.correct_pending_removal_minecraft_id(
            interaction,
            old_account_id=old_account.id,
            expected_discord_user_id=123,
            edition="java",
            minecraft_name="CorrectName",
        )

        corrected = store.get_by_server_player_name("CorrectName")
        assert corrected is not None
        assert corrected.discord_user_id == 123
        assert corrected.managed
        assert store.get(old_account.id).status == "pending_remove"  # type: ignore[union-attr]
        bot._add_to_whitelist.assert_not_awaited()
        interaction.response.edit_message.assert_awaited_once_with(
            content="⏳ Minecraft IDを確認しています…",
            view=None,
        )
        interaction.edit_original_response.assert_awaited_once()

    asyncio.run(exercise())


def test_server_announcement_is_also_sent_to_discord_log() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret", rcon_password="secret"))
        bot.validate_runtime_admin = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._execute_checked_rcon = AsyncMock(return_value="")  # type: ignore[method-assign]
        bot._send = AsyncMock()  # type: ignore[method-assign]
        bot._voice_player.enqueue = Mock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.guild_id = 456
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot.announce_server(interaction, "メンテナンスを開始します")

        bot._execute_checked_rcon.assert_awaited_once()
        bot._voice_player.enqueue.assert_called_once_with(  # type: ignore[attr-defined]
            456, "サーバー告知、メンテナンスを開始します"
        )
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
        bot._voice_player.enqueue = Mock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.guild_id = 456
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot.announce_server(interaction, "メンテナンスを開始します")

        bot._execute_checked_rcon.assert_awaited_once()
        bot._voice_player.enqueue.assert_called_once_with(  # type: ignore[attr-defined]
            456, "サーバー告知、メンテナンスを開始します"
        )
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
        bot._voice_player.enqueue = Mock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.guild_id = 456
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await bot.announce_server(interaction, "メンテナンスを開始します")

        bot._send.assert_not_awaited()  # type: ignore[attr-defined]
        bot._voice_player.enqueue.assert_not_called()  # type: ignore[attr-defined]
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
