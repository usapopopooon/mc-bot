from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import replace
from pathlib import Path

import discord
from discord import app_commands

from mc_bot.accounts import AccountStore, MinecraftAccount
from mc_bot.config import Config
from mc_bot.events import EventType, LogEvent, parse_log_line
from mc_bot.formatting import format_event
from mc_bot.player_count import (
    PLAYER_COUNT_CHANNEL_NAME,
    PLAYER_COUNT_DISABLED_STATUS,
    parse_online_player_count,
    player_count_status,
)
from mc_bot.rcon import RconClient, RconError
from mc_bot.server_admin import (
    announcement_command,
    clean_rcon_output,
    kick_command,
    parse_online_players,
    read_whitelist_enabled,
    read_whitelisted_players,
    validate_rcon_response,
)
from mc_bot.settings import RuntimeSettings, SettingsStore
from mc_bot.tailer import LogTailer
from mc_bot.translations import AdvancementTranslator
from mc_bot.ui import (
    AccessPanelView,
    AccountSelectView,
    AdminPanelView,
    ApprovalView,
    ConfirmRegistrationView,
    KickPlayerSelectView,
    ServerControlView,
    VoiceControlView,
    WhitelistControlView,
    access_panel_embed,
    admin_panel_embed,
)
from mc_bot.voice import MinecraftVoicePlayer, event_speech_text

LOGGER = logging.getLogger(__name__)
_JAVA_NAME = re.compile(r"[A-Za-z0-9_]{3,16}")
_VOICE_CONNECTED_SPEECH = "せつぞくしました"
_VOICE_CHECK_SPEECH = "マインクラフトの読み上げは正常に動作しています"


class MinecraftDiscordBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

        self._config = config
        self._translator = AdvancementTranslator.load()
        self._tailer = LogTailer(config.minecraft_log_path, config.cursor_path)
        self._settings_store = SettingsStore(config.settings_path)
        self._accounts = AccountStore(config.accounts_path)
        self._voice_player = MinecraftVoicePlayer(
            self,
            api_url=config.voicevox_tts_api_url,
            api_token=config.voicevox_tts_api_token,
            speaker_id=config.voicevox_speaker_id,
            speed=config.voicevox_speed,
        )
        self._rcon = (
            RconClient(
                config.rcon_host,
                config.rcon_port,
                config.rcon_password,
            )
            if config.rcon_password
            else None
        )
        try:
            self._settings = self._settings_store.load()
        except ValueError as error:
            LOGGER.error("Invalid settings; starting unconfigured: %s", error)
            self._settings = RuntimeSettings()
        self._settings_lock = asyncio.Lock()

        self._tailer_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._player_count_task: asyncio.Task[None] | None = None
        self._player_count_name_task: asyncio.Task[None] | None = None
        self._player_count_update_lock = asyncio.Lock()
        self._whitelist_operation_lock = asyncio.Lock()
        self._last_player_count_status: str | None = None
        self._channel: discord.TextChannel | None = None
        self._delivery_healthy = True
        self._closing = False
        self._health_path = Path("/tmp/mc-bot-healthy")
        self._sync_ticks = 0

    def _register_commands(self) -> None:
        group = app_commands.Group(
            name="mc-config",
            description="Minecraft Botの設定",
            default_permissions=discord.Permissions(manage_guild=True),
            guild_only=True,
        )
        group.command(
            name="channel",
            description="ログの通知先チャンネルを設定します",
        )(self._configure_channel)
        group.command(
            name="panel",
            description="Minecraft参加パネルを設置します",
        )(self._configure_access_panel)
        group.command(
            name="admin-panel",
            description="Minecraft管理パネルを設置します",
        )(self._configure_admin_panel)
        group.command(
            name="approval",
            description="参加登録の承認方式を設定します",
        )(self._configure_approval)
        group.command(
            name="player-count",
            description="オンライン人数チャンネルを管理します",
        )(self._configure_player_count)
        group.command(
            name="show",
            description="現在のBot設定と稼働状態を表示します",
        )(self._show_configuration)
        self.tree.add_command(group)
        self.tree.command(
            name="vc",
            description="Minecraft読み上げを現在のVCで開始します",
        )(self._voice_command)

    async def setup_hook(self) -> None:
        await asyncio.to_thread(self._accounts.initialize)
        self._voice_player.start()
        self.add_view(AccessPanelView(self))
        self.add_view(AdminPanelView(self))
        for account in await asyncio.to_thread(self._accounts.list_pending_approvals):
            if account.approval_message_id is not None:
                self.add_view(
                    ApprovalView(self, account.id),
                    message_id=account.approval_message_id,
                )
        synced = await self.tree.sync()
        LOGGER.info("Synced %d global Discord application commands", len(synced))

    async def on_ready(self) -> None:
        await self._sync_whitelist_accounts()
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_loop(), name="health-monitor")

        await self._refresh_admin_panel()
        channel_id = self._settings.channel_id
        if channel_id is None:
            self._channel = None
            LOGGER.warning(
                "Notification channel is not configured; use /mc-config channel in Discord"
            )
        else:
            try:
                self._channel = await self._resolve_and_validate_channel(
                    channel_id, require_embeds=True
                )
                await self._ensure_tailer_started()
            except (OSError, RuntimeError, discord.DiscordException) as error:
                self._channel = None
                LOGGER.error(
                    "Notification forwarding is inactive; repair it with /mc-config channel: %s",
                    error,
                )

        if self._settings.player_count_enabled:
            self._schedule_player_count_refresh(delay=0)
            self._schedule_player_count_name_normalization()
        if self._settings.voice_enabled:
            await self._restore_voice_connection()

        LOGGER.info(
            "Discord connected as %s; loaded %d advancement translations",
            self.user,
            len(self._translator),
        )

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != self._settings.guild_id:
            return
        accounts = await asyncio.to_thread(self._accounts.list_managed_for_discord_user, member.id)
        for account in accounts:
            try:
                await self._remove_from_whitelist(account)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                await asyncio.to_thread(self._accounts.update_status, account.id, "pending_remove")
                LOGGER.error(
                    "Could not revoke Minecraft account %s after Discord departure: %s",
                    account.minecraft_name,
                    error,
                )

    async def close(self) -> None:
        self._closing = True
        self._remove_health_file()
        if self._health_task is not None:
            self._health_task.cancel()
            await asyncio.gather(self._health_task, return_exceptions=True)
            self._health_task = None
        if self._player_count_task is not None:
            self._player_count_task.cancel()
            await asyncio.gather(self._player_count_task, return_exceptions=True)
            self._player_count_task = None
        if self._player_count_name_task is not None:
            self._player_count_name_task.cancel()
            await asyncio.gather(self._player_count_name_task, return_exceptions=True)
            self._player_count_name_task = None
        if self._tailer_task is not None:
            self._tailer_task.cancel()
            await asyncio.gather(self._tailer_task, return_exceptions=True)
            self._tailer_task = None
        await self._voice_player.close()
        await super().close()

    @app_commands.describe(channel="通知先。省略時はコマンドを実行したチャンネル")
    async def _configure_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "通知先にはサーバーのテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            validated_channel = await self._resolve_and_validate_channel(
                target.id, require_embeds=True
            )
            await asyncio.to_thread(self._tailer.validate)
            async with self._settings_lock:
                updated = replace(
                    self._settings,
                    channel_id=target.id,
                    guild_id=target.guild.id,
                )
                await asyncio.to_thread(self._settings_store.save, updated)
                self._settings = updated
                self._channel = validated_channel
            await self._ensure_tailer_started()
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure notification channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
            return

        await interaction.followup.send(
            f"Minecraftログの通知先を {target.mention} に設定しました。",
            ephemeral=True,
        )

    @app_commands.describe(channel="参加パネルの投稿先。省略時は現在のチャンネル")
    async def _configure_access_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._configure_panel(interaction, channel, admin=False)

    @app_commands.describe(channel="管理パネルの投稿先。省略時は現在のチャンネル")
    async def _configure_admin_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._configure_panel(interaction, channel, admin=True)

    @app_commands.describe(
        mode="自動承認または管理者承認",
        channel="管理者承認時に申請を投稿するチャンネル",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="自動承認", value="automatic"),
            app_commands.Choice(name="管理者承認", value="manual"),
        ]
    )
    async def _configure_approval(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        approval_channel_id = self._settings.approval_channel_id
        if mode.value == "manual":
            try:
                target = channel
                if target is None and approval_channel_id is not None:
                    target = await self._resolve_and_validate_channel(
                        approval_channel_id, require_embeds=True
                    )
                if target is None:
                    await interaction.followup.send(
                        "管理者承認では申請の投稿先チャンネルを指定してください。",
                        ephemeral=True,
                    )
                    return
                self._ensure_same_guild(target.guild.id)
                await self._resolve_and_validate_channel(target.id, require_embeds=True)
            except (RuntimeError, discord.DiscordException) as error:
                await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
                return
            approval_channel_id = target.id

        updated = replace(
            self._settings,
            guild_id=interaction.guild_id,
            approval_mode=mode.value,
            approval_channel_id=approval_channel_id,
        )
        await self._save_settings(updated)
        await self._refresh_access_panel()
        label = "自動承認" if mode.value == "automatic" else "管理者承認"
        await interaction.followup.send(f"承認方式を「{label}」に設定しました。", ephemeral=True)

    @app_commands.describe(action="人数表示チャンネルに対する操作")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="有効化", value="enable"),
            app_commands.Choice(name="更新停止", value="disable"),
            app_commands.Choice(name="チャンネル削除", value="remove"),
        ]
    )
    async def _configure_player_count(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True)

        try:
            self._ensure_same_guild(guild.id)
            if action.value == "enable":
                channel = await self._enable_player_count_channel(interaction, guild)
                await interaction.followup.send(
                    f"オンライン人数表示を {channel.mention} で開始しました。",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif action.value == "disable":
                channel = await self._get_player_count_channel(guild)
                updated = replace(
                    self._settings,
                    guild_id=guild.id,
                    player_count_enabled=False,
                )
                await self._save_settings(updated)
                if channel is not None:
                    async with self._player_count_update_lock:
                        await channel.edit(
                            status=PLAYER_COUNT_DISABLED_STATUS,
                            reason="Minecraftオンライン人数表示を停止",
                        )
                        self._last_player_count_status = PLAYER_COUNT_DISABLED_STATUS
                await interaction.followup.send(
                    "オンライン人数の更新を停止しました。",
                    ephemeral=True,
                )
            else:
                channel = await self._get_player_count_channel(guild)
                if channel is not None:
                    await channel.delete(reason="Minecraftオンライン人数チャンネルを削除")
                updated = replace(
                    self._settings,
                    guild_id=guild.id,
                    player_count_channel_id=None,
                    player_count_enabled=False,
                )
                await self._save_settings(updated)
                await interaction.followup.send(
                    "オンライン人数チャンネルを削除しました。",
                    ephemeral=True,
                )
        except (OSError, RuntimeError, ValueError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure player count channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)

    async def _show_configuration(self, interaction: discord.Interaction) -> None:
        if not await self._require_server_manager(interaction):
            return
        forwarding = (
            self._channel is not None
            and self._tailer_task is not None
            and not self._tailer_task.done()
            and self._delivery_healthy
        )
        registered, unlinked, pending = await asyncio.to_thread(self._accounts.count_summary)
        mode = "自動承認" if self._settings.approval_mode == "automatic" else "管理者承認"
        await interaction.response.send_message(
            "\n".join(
                (
                    f"ログ通知先: {self._channel_text(self._settings.channel_id)}",
                    f"参加パネル: {self._channel_text(self._settings.panel_channel_id)}",
                    f"管理パネル: {self._channel_text(self._settings.admin_panel_channel_id)}",
                    f"承認方式: {mode}",
                    f"申請確認先: {self._channel_text(self._settings.approval_channel_id)}",
                    "人数表示: "
                    f"{self._channel_text(self._settings.player_count_channel_id)} "
                    f"({'稼働中' if self._settings.player_count_enabled else '停止中'})",
                    "Minecraft読み上げ: "
                    f"{self._channel_text(self._settings.voice_channel_id)} "
                    f"({'稼働中' if self._settings.voice_enabled else '停止中'})",
                    f"ログ転送: {'稼働中' if forwarding else '停止中'}",
                    f"登録: {registered}件 (未連携 {unlinked}件、承認待ち {pending}件)",
                    f"RCON: {'設定済み' if self._rcon is not None else '未設定'}",
                )
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _configure_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        *,
        admin: bool,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "パネルの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(target.id, require_embeds=True)
            old_channel_id = (
                self._settings.admin_panel_channel_id if admin else self._settings.panel_channel_id
            )
            old_message_id = (
                self._settings.admin_panel_message_id if admin else self._settings.panel_message_id
            )
            embed = (
                admin_panel_embed() if admin else access_panel_embed(self._settings.approval_mode)
            )
            view: discord.ui.View = AdminPanelView(self) if admin else AccessPanelView(self)
            message: discord.Message | None = None
            if old_channel_id == target.id and old_message_id is not None:
                try:
                    message = await target.fetch_message(old_message_id)
                    await message.edit(embed=embed, view=view)
                except discord.NotFound:
                    message = None
            if message is None:
                message = await target.send(embed=embed, view=view)
                await self._disable_old_panel(old_channel_id, old_message_id)

            fields = (
                {
                    "admin_panel_channel_id": target.id,
                    "admin_panel_message_id": message.id,
                }
                if admin
                else {
                    "panel_channel_id": target.id,
                    "panel_message_id": message.id,
                }
            )
            updated = replace(
                self._settings,
                guild_id=target.guild.id,
                **fields,
            )
            await self._save_settings(updated)
        except (RuntimeError, discord.DiscordException) as error:
            await interaction.followup.send(f"設置できませんでした: {error}", ephemeral=True)
            return
        name = "管理パネル" if admin else "参加パネル"
        await interaction.followup.send(
            f"{name}を {target.mention} に設置しました。", ephemeral=True
        )

    async def validate_panel_interaction(
        self, interaction: discord.Interaction, *, admin: bool
    ) -> bool:
        expected_message_id = (
            self._settings.admin_panel_message_id if admin else self._settings.panel_message_id
        )
        if interaction.message is None or interaction.message.id != expected_message_id:
            await interaction.response.send_message(
                "このパネルは現在使用されていません。最新のパネルをご利用ください。",
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self._settings.guild_id:
            await interaction.response.send_message(
                "このDiscordサーバーでは利用できません。", ephemeral=True
            )
            return False
        if admin:
            return await self._require_server_manager(interaction)
        if not isinstance(interaction.user, discord.Member) or interaction.user.bot:
            await interaction.response.send_message(
                "Discordサーバーのメンバーだけが利用できます。", ephemeral=True
            )
            return False
        return True

    async def confirm_registration(
        self,
        interaction: discord.Interaction,
        *,
        edition: str,
        minecraft_name: str,
        target: discord.Member,
        source: str,
    ) -> None:
        try:
            normalized_name, _ = self._normalize_player_name(edition, minecraft_name)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        edition_label = "Java版" if edition == "java" else "Bedrock版"
        target_line = f"\nDiscordユーザー: {target.mention}" if source == "admin" else ""
        await interaction.response.send_message(
            f"次の内容で登録します。\n\n"
            f"エディション: **{edition_label}**\n"
            f"アカウント名: **{discord.utils.escape_markdown(normalized_name)}**"
            f"{target_line}",
            view=ConfirmRegistrationView(
                self,
                owner_id=interaction.user.id,
                target=target,
                edition=edition,
                minecraft_name=normalized_name,
                source=source,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def register_account(
        self,
        interaction: discord.Interaction,
        *,
        edition: str,
        minecraft_name: str,
        target: discord.Member,
        source: str,
    ) -> None:
        _, server_name = self._normalize_player_name(edition, minecraft_name)
        automatic = self._settings.approval_mode == "automatic" or source == "admin"
        status = "pending_add" if automatic else "pending_approval"
        try:
            account = await asyncio.to_thread(
                self._accounts.create_registration,
                edition=edition,
                minecraft_name=minecraft_name,
                server_player_name=server_name,
                discord_user_id=target.id,
                discord_username=target.name,
                source=source,
                status=status,
                created_by=interaction.user.id,
            )
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        if not automatic:
            try:
                await self._post_approval(account, target)
            except (RuntimeError, discord.DiscordException) as error:
                await asyncio.to_thread(self._accounts.delete_pending, account.id)
                await interaction.followup.send(
                    f"申請を送信できませんでした: {error}", ephemeral=True
                )
                return
            await interaction.followup.send(
                "参加申請を送信しました。管理者の承認をお待ちください。",
                ephemeral=True,
            )
            return

        try:
            await self._add_to_whitelist(account)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not add account through RCON: %s", error)
            await interaction.followup.send(
                "登録を保存しましたが、Minecraftへの反映待ちです。管理者に通知してください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ **{discord.utils.escape_markdown(minecraft_name)}** を登録しました。"
            "\nMinecraftサーバーへ参加できます。",
            ephemeral=True,
        )
        if source == "admin" and target.id != interaction.user.id:
            await self._notify_target(target, account)

    async def show_user_accounts(self, interaction: discord.Interaction) -> None:
        accounts = await asyncio.to_thread(
            self._accounts.list_for_discord_user, interaction.user.id
        )
        if not accounts:
            await interaction.response.send_message(
                "登録済みのMinecraftアカウントはありません。",
                ephemeral=True,
            )
            return
        lines = [self._account_line(account) for account in accounts]
        removable = [
            account
            for account in accounts
            if account.status in {"active", "pending_approval", "pending_add"}
        ][:25]
        view = AccountSelectView(self, removable, "remove") if removable else None
        await interaction.response.send_message(
            "あなたのMinecraftアカウント\n\n" + "\n".join(lines),
            view=view,
            ephemeral=True,
        )

    async def show_unlinked_accounts(self, interaction: discord.Interaction) -> None:
        await self._import_whitelist()
        accounts = await asyncio.to_thread(self._accounts.list_unlinked)
        if not accounts:
            await interaction.response.send_message(
                "未連携の既存whitelistはありません。", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Discordアカウントへ紐付ける既存whitelistを選択してください。"
            "\n初期状態ではwhitelistから削除されない保護対象です。",
            view=AccountSelectView(self, accounts, "link"),
            ephemeral=True,
        )

    async def link_existing_account(
        self,
        interaction: discord.Interaction,
        account_id: int,
        target: discord.Member,
        *,
        managed: bool,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        try:
            account = await asyncio.to_thread(
                self._accounts.link_existing,
                account_id,
                discord_user_id=target.id,
                discord_username=target.name,
                managed=managed,
                created_by=interaction.user.id,
            )
        except ValueError as error:
            await interaction.response.edit_message(content=str(error), view=None)
            return
        policy = "Discord退会時に自動削除" if managed else "whitelistを保護"
        await interaction.response.edit_message(
            content=(
                f"**{discord.utils.escape_markdown(account.minecraft_name)}** を "
                f"{target.mention} に紐付けました。\n管理方法: {policy}"
            ),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def remove_account(self, interaction: discord.Interaction, account_id: int) -> None:
        account = await asyncio.to_thread(self._accounts.get, account_id)
        is_manager = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild
        )
        if (account is None or account.discord_user_id != interaction.user.id) and not is_manager:
            await interaction.response.edit_message(
                content="このアカウントは解除できません。", view=None
            )
            return
        if account is None:
            await interaction.response.edit_message(content="登録が見つかりません。", view=None)
            return
        if not account.managed:
            await asyncio.to_thread(self._accounts.unlink_protected, account.id)
            await interaction.response.edit_message(
                content=(
                    f"**{discord.utils.escape_markdown(account.minecraft_name)}** の紐付けを"
                    "解除しました。既存whitelistは保護されています。"
                ),
                view=None,
            )
            return
        if account.status == "pending_approval":
            await asyncio.to_thread(self._accounts.delete_pending, account.id)
            await interaction.response.edit_message(content="申請を取り消しました。", view=None)
            return
        await interaction.response.defer()
        try:
            await self._remove_from_whitelist(account)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await asyncio.to_thread(self._accounts.update_status, account.id, "pending_remove")
            await interaction.edit_original_response(
                content=f"解除を保存しましたが、Minecraftへの反映待ちです: {error}",
                view=None,
            )
            return
        await interaction.edit_original_response(
            content=f"**{discord.utils.escape_markdown(account.minecraft_name)}** を解除しました。",
            view=None,
        )

    async def process_approval(
        self, interaction: discord.Interaction, account_id: int, *, approved: bool
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        account = await asyncio.to_thread(self._accounts.get, account_id)
        if account is None or account.status != "pending_approval":
            await interaction.response.send_message(
                "この申請はすでに処理されています。", ephemeral=True
            )
            return
        if not approved:
            await asyncio.to_thread(self._accounts.update_status, account.id, "rejected")
            await interaction.response.edit_message(
                embed=self._approval_embed(account, "却下済み"),
                view=None,
            )
            return
        if account.discord_user_id is None:
            await interaction.response.send_message("申請者が見つかりません。", ephemeral=True)
            return
        guild = interaction.guild
        if guild is None:
            return
        try:
            target = guild.get_member(account.discord_user_id) or await guild.fetch_member(
                account.discord_user_id
            )
        except discord.NotFound:
            await interaction.response.send_message(
                "申請者はDiscordサーバーに参加していません。", ephemeral=True
            )
            return
        await interaction.response.defer()
        await asyncio.to_thread(self._accounts.update_status, account.id, "pending_add")
        try:
            await self._add_to_whitelist(account)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(
                f"Minecraftへ反映できませんでした: {error}", ephemeral=True
            )
            return
        await interaction.edit_original_response(
            embed=self._approval_embed(account, "承認済み"),
            view=None,
        )
        await self._notify_target(target, account)

    async def show_admin_summary(self, interaction: discord.Interaction) -> None:
        registered, unlinked, pending = await asyncio.to_thread(self._accounts.count_summary)
        registrations = await asyncio.to_thread(self._accounts.list_whitelist_registrations)
        try:
            player_names = await asyncio.to_thread(
                read_whitelisted_players,
                self._config.minecraft_whitelist_path,
            )
            present = {name.casefold() for name in player_names}
            unreflected = sum(
                account.status in {"active", "pending_add"}
                and account.server_player_name.casefold() not in present
                for account in registrations
            )
            actual_line = f"実Whitelist: **{len(player_names)}件**\n未反映: **{unreflected}件**\n"
        except ValueError:
            actual_line = "実Whitelist: **取得失敗**\n"
        await interaction.response.send_message(
            f"登録情報: **{registered}件**\n"
            f"{actual_line}"
            f"未連携・保護: **{unlinked}件**\n"
            f"承認待ち: **{pending}件**",
            ephemeral=True,
        )

    async def show_whitelist_entries(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            player_names = await asyncio.to_thread(
                read_whitelisted_players,
                self._config.minecraft_whitelist_path,
            )
        except ValueError as error:
            await interaction.followup.send(
                f"Whitelist一覧を取得できませんでした: {error}",
                ephemeral=True,
            )
            return

        registrations = await asyncio.to_thread(self._accounts.list_whitelist_registrations)
        actual_names = {name.casefold(): name for name in player_names}
        registrations_by_name = {
            account.server_player_name.casefold(): account for account in registrations
        }

        def display_name(key: str) -> str:
            account = registrations_by_name.get(key)
            return actual_names.get(key) or (account.server_player_name if account else key)

        all_names = sorted(
            actual_names.keys() | registrations_by_name.keys(),
            key=lambda key: display_name(key).casefold(),
        )

        if not all_names:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🛡️ Whitelist一覧",
                    description="登録者はいません。",
                    color=discord.Color.blurple(),
                ),
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for normalized_name in all_names:
            account = registrations_by_name.get(normalized_name)
            player_name = display_name(normalized_name)
            is_present = normalized_name in actual_names
            edition = (
                account.edition
                if account is not None
                else (
                    "bedrock"
                    if self._config.floodgate_username_prefix
                    and player_name.startswith(self._config.floodgate_username_prefix)
                    else "java"
                )
            )
            edition_label = "🪨 Bedrock" if edition == "bedrock" else "☕ Java"
            escaped_name = discord.utils.escape_markdown(player_name)
            if account is not None and account.discord_user_id is not None:
                account_text = f"**{escaped_name} (<@{account.discord_user_id}>)**"
            else:
                account_text = f"**{escaped_name}** (未連携)"
            state = ""
            if not is_present:
                state = "  ⚠️ Whitelist未反映"
            elif account is not None and account.status == "pending_remove":
                state = "  ⚠️ 削除反映待ち"
            lines.append(f"{edition_label}  {account_text}{state}")

        embeds: list[discord.Embed] = []
        total = len(lines)
        actual_count = len(player_names)
        registered_count = len(registrations)
        for offset in range(0, total, 20):
            page = discord.Embed(
                title=(
                    f"🛡️ Whitelist一覧 (実登録{actual_count}件 / 登録情報{registered_count}件)"
                    if offset == 0
                    else "🛡️ Whitelist一覧 (続き)"
                ),
                description="\n".join(lines[offset : offset + 20]),
                color=discord.Color.blurple(),
            )
            page.set_footer(text=f"{offset + 1}-{min(offset + 20, total)} / {total}")
            embeds.append(page)

        for embed in embeds:
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.guild_only()
    async def _voice_command(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        voice_state = member.voice if isinstance(member, discord.Member) else None
        channel = voice_state.channel if voice_state is not None else None
        if isinstance(channel, discord.VoiceChannel):
            await self.configure_voice_channel(interaction, channel)
            return
        await interaction.response.send_message(
            "先に接続させたいVCへ参加してから `/vc` を実行してください。",
            ephemeral=True,
        )

    async def show_voice_controls(self, interaction: discord.Interaction) -> None:
        channel_id = self._settings.voice_channel_id
        status = "停止中"
        if self._settings.voice_enabled and channel_id is not None:
            status = f"接続先: <#{channel_id}>"
        api_status = "設定済み" if self._voice_player.configured else "APIトークン未設定"
        await interaction.response.send_message(
            f"Minecraft読み上げ: **{status}**\nVOICEVOX API: **{api_status}**\n\n"
            "接続先のVCを選択すると、チャット・参加・退出・進捗を読み上げます。",
            view=VoiceControlView(self, interaction.user.id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def configure_voice_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not self._voice_player.configured:
            await interaction.edit_original_response(
                content="VOICEVOX_TTS_API_TOKENが設定されていません。",
                view=None,
            )
            return
        try:
            self._ensure_same_guild(channel.guild.id)
            await self._connect_voice_channel(channel)
            await self._save_settings(
                replace(
                    self._settings,
                    voice_channel_id=channel.id,
                    voice_enabled=True,
                )
            )
            self._voice_player.enqueue(channel.guild.id, _VOICE_CONNECTED_SPEECH)
        except (OSError, RuntimeError, discord.DiscordException) as error:
            await interaction.edit_original_response(
                content=f"VCへ接続できませんでした: {error}",
                view=None,
            )
            return
        self._audit_server_action(interaction, f"voice connect channel_id={channel.id}")
        await interaction.edit_original_response(
            content=f"✅ {channel.mention} でMinecraft読み上げを開始しました。",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def disconnect_voice(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        try:
            if guild is not None and guild.voice_client is not None:
                await guild.voice_client.disconnect(force=True)
            await self._save_settings(
                replace(
                    self._settings,
                    voice_channel_id=None,
                    voice_enabled=False,
                )
            )
        except (OSError, discord.DiscordException) as error:
            await interaction.edit_original_response(
                content=f"読み上げを停止できませんでした: {error}",
                view=None,
            )
            return
        self._audit_server_action(interaction, "voice disconnect")
        await interaction.edit_original_response(
            content="Minecraft読み上げを停止し、VCから切断しました。",
            view=None,
        )

    async def test_voice(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if (
            not self._settings.voice_enabled
            or guild_id is None
            or not self._voice_player.is_connected(guild_id)
        ):
            await interaction.response.send_message(
                "VCへ接続されていません。接続先を選び直してください。",
                ephemeral=True,
            )
            return
        if not self._voice_player.enqueue(guild_id, _VOICE_CHECK_SPEECH):
            await interaction.response.send_message(
                "読み上げキューへ追加できませんでした。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "読み上げ確認音声をキューへ追加しました。", ephemeral=True
        )

    async def validate_runtime_admin(self, interaction: discord.Interaction) -> bool:
        return await self._require_server_manager(interaction)

    async def show_server_control(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            embed = await self._server_control_embed()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(
                f"Minecraftサーバーの状態を取得できませんでした: {error}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=embed,
            view=ServerControlView(self, interaction.user.id),
            ephemeral=True,
        )

    async def refresh_server_control(
        self,
        interaction: discord.Interaction,
        view: ServerControlView,
    ) -> None:
        await interaction.response.defer()
        try:
            embed = await self._server_control_embed()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"更新できませんでした: {error}", ephemeral=True)
            return
        await interaction.edit_original_response(embed=embed, view=view)

    async def show_online_players(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            players = await self._online_players()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"取得できませんでした: {error}", ephemeral=True)
            return
        description = (
            "オンラインプレイヤーはいません。"
            if not players
            else "\n".join(f"・**{discord.utils.escape_markdown(name)}**" for name in players)
        )
        await interaction.followup.send(
            embed=discord.Embed(
                title=f"👥 オンライン {len(players)}人",
                description=description,
                color=discord.Color.green() if players else discord.Color.light_grey(),
            ),
            ephemeral=True,
        )

    async def show_kick_player_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            players = await self._online_players()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"取得できませんでした: {error}", ephemeral=True)
            return
        if not players:
            await interaction.followup.send("オンラインプレイヤーはいません。", ephemeral=True)
            return
        await interaction.followup.send(
            "キックするプレイヤーを選択してください。",
            view=KickPlayerSelectView(self, interaction.user.id, players[:25]),
            ephemeral=True,
        )

    async def kick_online_player(
        self,
        interaction: discord.Interaction,
        player_name: str,
        reason: str,
    ) -> None:
        await interaction.response.defer()
        try:
            players = await self._online_players()
            if player_name not in players:
                raise ValueError("そのプレイヤーはすでにオフラインです")
            await self._execute_checked_rcon(kick_command(player_name, reason))
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"キックできませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, f"kick player={player_name}")
        await interaction.edit_original_response(
            content=f"✅ **{discord.utils.escape_markdown(player_name)}** をキックしました。",
            view=None,
        )

    async def announce_server(self, interaction: discord.Interaction, message: str) -> None:
        if not await self.validate_runtime_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self._execute_checked_rcon(announcement_command(message))
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"告知できませんでした: {error}", ephemeral=True)
            return
        self._audit_server_action(interaction, "announcement")
        await interaction.followup.send("✅ サーバー内へ告知しました。", ephemeral=True)

    async def show_whitelist_controls(self, interaction: discord.Interaction) -> None:
        try:
            state = await self._whitelist_state_text()
        except ValueError as error:
            await interaction.response.send_message(
                f"Whitelistの実状態を取得できませんでした: {error}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Whitelistの現在状態: **{state}**\n\n停止中はwhitelist未登録者も接続できます。",
            view=WhitelistControlView(self, interaction.user.id),
            ephemeral=True,
        )

    async def pause_whitelist(self, interaction: discord.Interaction, minutes: int) -> None:
        if minutes not in {15, 30, 60}:
            await interaction.response.send_message("無効な停止時間です。", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            resume_at = await self._pause_whitelist_for(minutes)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"停止できませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, f"whitelist pause minutes={minutes}")
        await interaction.edit_original_response(
            content=(
                f"⚠️ Whitelistを{minutes}分間停止しました。<t:{int(resume_at)}:R>に自動再開します。"
            ),
            view=None,
        )

    async def resume_whitelist(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            await self._resume_whitelist_now()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"再開できませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, "whitelist resume")
        await interaction.edit_original_response(content="✅ Whitelistを再開しました。", view=None)

    async def change_world(
        self,
        interaction: discord.Interaction,
        command: str,
        description: str,
    ) -> None:
        allowed = {
            "weather clear": "天候を晴れ",
            "weather rain": "天候を雨",
            "weather thunder": "天候を雷雨",
            "time set day": "時刻を朝",
            "time set night": "時刻を夜",
        }
        if allowed.get(command) != description:
            await interaction.response.send_message("許可されていない操作です。", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self._execute_checked_rcon(command)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"変更できませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, command)
        await interaction.edit_original_response(
            content=f"✅ {description}に変更しました。", view=None
        )

    async def show_performance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            response = await self._execute_checked_rcon("spark health --memory")
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"取得できませんでした: {error}", ephemeral=True)
            return
        output = clean_rcon_output(response, limit=3800).replace("```", "'''")
        await interaction.followup.send(
            embed=discord.Embed(
                title="📊 Minecraftパフォーマンス",
                description=f"```text\n{output or '応答がありませんでした'}\n```",
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )

    async def _server_control_embed(self) -> discord.Embed:
        players = await self._online_players()
        whitelist = await self._whitelist_state_text()
        names = (
            "、".join(discord.utils.escape_markdown(name) for name in players)
            if players
            else "なし"
        )
        embed = discord.Embed(
            title="🎮 Minecraft サーバー操作",
            description="🟢 サーバー稼働中",
            color=discord.Color.green(),
        )
        embed.add_field(name="オンライン", value=f"**{len(players)}人**", inline=True)
        embed.add_field(name="Whitelist", value=whitelist, inline=True)
        embed.add_field(name="プレイヤー", value=names[:1024], inline=False)
        embed.set_footer(text="表示内容はボタンを押した時点の状態です")
        return embed

    async def _online_players(self) -> list[str]:
        return parse_online_players(await self._execute_rcon("list"))

    async def _execute_rcon(self, command: str) -> str:
        return await asyncio.to_thread(self._require_rcon().execute, command)

    async def _execute_checked_rcon(self, command: str) -> str:
        return validate_rcon_response(await self._execute_rcon(command))

    async def _read_whitelist_enabled(self) -> bool:
        return await asyncio.to_thread(
            read_whitelist_enabled,
            self._config.minecraft_server_properties_path,
        )

    async def _wait_for_whitelist_state(self, expected: bool) -> None:
        for attempt in range(10):
            if await self._read_whitelist_enabled() is expected:
                return
            if attempt < 9:
                await asyncio.sleep(0.2)
        label = "有効" if expected else "無効"
        raise RuntimeError(f"Whitelistの実状態が{label}になりませんでした")

    async def _set_whitelist_enabled(self, enabled: bool) -> None:
        command = "whitelist on" if enabled else "whitelist off"
        await self._execute_checked_rcon(command)
        await self._wait_for_whitelist_state(enabled)

    async def _pause_whitelist_for(self, minutes: int) -> float:
        async with self._whitelist_operation_lock:
            resume_at = time.time() + minutes * 60
            await self._save_settings(replace(self._settings, whitelist_resume_at=resume_at))
            try:
                await self._set_whitelist_enabled(False)
            except OSError, RconError, RuntimeError, ValueError:
                try:
                    await self._set_whitelist_enabled(True)
                except OSError, RconError, RuntimeError, ValueError:
                    LOGGER.exception(
                        "Whitelist pause failed and immediate safety recovery also failed"
                    )
                    raise
                await self._save_settings(replace(self._settings, whitelist_resume_at=None))
                raise
            return resume_at

    async def _resume_whitelist_now(self) -> None:
        async with self._whitelist_operation_lock:
            await self._set_whitelist_enabled(True)
            await self._save_settings(replace(self._settings, whitelist_resume_at=None))

    async def _whitelist_state_text(self) -> str:
        async with self._whitelist_operation_lock:
            enabled = await self._read_whitelist_enabled()
            resume_at = self._settings.whitelist_resume_at
        if not enabled and resume_at is not None:
            return f"一時停止中・<t:{int(resume_at)}:R>に自動再開"
        if not enabled:
            return "⚠️ 無効・自動再開予定なし"
        if resume_at is not None:
            return f"有効・一時停止の再反映待ち (<t:{int(resume_at)}:R>に再開)"
        return "有効"

    async def _resume_whitelist_if_due(self) -> None:
        async with self._whitelist_operation_lock:
            resume_at = self._settings.whitelist_resume_at
            if resume_at is None:
                return
            try:
                if time.time() < resume_at:
                    if await self._read_whitelist_enabled():
                        await self._set_whitelist_enabled(False)
                    return
                await self._set_whitelist_enabled(True)
                await self._save_settings(replace(self._settings, whitelist_resume_at=None))
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning("Could not reconcile Minecraft whitelist pause: %s", error)
                return
            LOGGER.info("Minecraft whitelist automatically resumed")

    @staticmethod
    def _audit_server_action(interaction: discord.Interaction, action: str) -> None:
        LOGGER.info(
            "Minecraft admin action user_id=%d guild_id=%s action=%s",
            interaction.user.id,
            interaction.guild_id,
            action,
        )

    async def _post_approval(self, account: MinecraftAccount, target: discord.Member) -> None:
        channel_id = self._settings.approval_channel_id
        if channel_id is None:
            raise RuntimeError("申請確認先が設定されていません")
        channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
        message = await channel.send(
            embed=self._approval_embed(account, "承認待ち", target),
            view=ApprovalView(self, account.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await asyncio.to_thread(self._accounts.set_approval_message, account.id, message.id)

    def _approval_embed(
        self,
        account: MinecraftAccount,
        state: str,
        target: discord.Member | None = None,
    ) -> discord.Embed:
        username = target.name if target is not None else account.discord_username or "不明"
        user_id = target.id if target is not None else account.discord_user_id
        edition = "Java版" if account.edition == "java" else "Bedrock版"
        color = (
            discord.Color.orange()
            if state == "承認待ち"
            else discord.Color.green()
            if state == "承認済み"
            else discord.Color.red()
        )
        return discord.Embed(
            title=f"Minecraft参加申請・{state}",
            description=(
                f"Discord: @{discord.utils.escape_markdown(username)} (`{user_id}`)\n"
                f"エディション: {edition}\n"
                f"アカウント名: **{discord.utils.escape_markdown(account.minecraft_name)}**"
            ),
            color=color,
        )

    async def _add_to_whitelist(self, account: MinecraftAccount) -> None:
        command = (
            f"whitelist add {account.minecraft_name}"
            if account.edition == "java"
            else f'fwhitelist add "{account.minecraft_name}"'
        )
        await self._ensure_player_whitelist_state(account, expected=True, command=command)
        await asyncio.to_thread(self._accounts.update_status, account.id, "active")

    async def _remove_from_whitelist(self, account: MinecraftAccount) -> None:
        rcon = self._require_rcon()
        command = (
            f"whitelist remove {account.minecraft_name}"
            if account.edition == "java"
            else f'fwhitelist remove "{account.minecraft_name}"'
        )
        await self._ensure_player_whitelist_state(account, expected=False, command=command)
        try:
            await asyncio.to_thread(
                rcon.execute,
                f'kick "{account.server_player_name}" Discordの参加登録が解除されました',
            )
        except OSError, RconError:
            LOGGER.debug("Could not kick %s; player may be offline", account.server_player_name)
        await asyncio.to_thread(self._accounts.update_status, account.id, "missing")

    async def _ensure_player_whitelist_state(
        self,
        account: MinecraftAccount,
        *,
        expected: bool,
        command: str,
    ) -> None:
        async with self._whitelist_operation_lock:
            if await self._player_is_whitelisted(account.server_player_name) is expected:
                return
            await self._execute_checked_rcon(command)
            for attempt in range(20):
                if await self._player_is_whitelisted(account.server_player_name) is expected:
                    return
                if attempt < 19:
                    await asyncio.sleep(0.25)
        state = "追加" if expected else "削除"
        raise RuntimeError(f"{account.server_player_name}のWhitelist{state}を確認できませんでした")

    async def _player_is_whitelisted(self, player_name: str) -> bool:
        player_names = await asyncio.to_thread(
            read_whitelisted_players,
            self._config.minecraft_whitelist_path,
        )
        normalized_name = player_name.casefold()
        return any(name.casefold() == normalized_name for name in player_names)

    def _require_rcon(self) -> RconClient:
        if self._rcon is None:
            raise RuntimeError("Minecraft RCONが設定されていません")
        return self._rcon

    def _normalize_player_name(self, edition: str, value: str) -> tuple[str, str]:
        name = value.strip()
        if edition == "java":
            if not _JAVA_NAME.fullmatch(name):
                raise ValueError(
                    "Java版の名前は3から16文字の半角英数字またはアンダースコアで入力してください。"
                )
            return name, name
        name = name.removeprefix(self._config.floodgate_username_prefix).strip()
        if not 1 <= len(name) <= 32 or any(
            character in name for character in ('"', "\\", "\n", "\r", "\0")
        ):
            raise ValueError("Bedrock版のゲーマータグを正しく入力してください。")
        server_name = f"{self._config.floodgate_username_prefix}{name.replace(' ', '_')}"
        return name, server_name

    async def _notify_target(self, target: discord.Member, account: MinecraftAccount) -> None:
        try:
            await target.send(
                "✅ Minecraftアカウント "
                f"**{discord.utils.escape_markdown(account.minecraft_name)}**"
                " の参加登録が完了しました。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.DiscordException:
            LOGGER.info("Could not DM registration result to Discord user %d", target.id)

    async def _import_whitelist(self) -> None:
        try:
            await asyncio.to_thread(
                self._accounts.import_whitelist,
                self._config.minecraft_whitelist_path,
                self._config.floodgate_username_prefix,
            )
        except (OSError, ValueError) as error:
            LOGGER.warning("Could not import Minecraft whitelist: %s", error)

    async def _sync_whitelist_accounts(self) -> None:
        await self._import_whitelist()
        try:
            player_names = await asyncio.to_thread(
                read_whitelisted_players,
                self._config.minecraft_whitelist_path,
            )
        except ValueError as error:
            LOGGER.warning("Could not reconcile Minecraft whitelist registrations: %s", error)
            return
        changes = await asyncio.to_thread(self._accounts.reconcile_whitelist, player_names)
        if any(changes):
            LOGGER.info(
                "Reconciled Minecraft whitelist registrations queued_adds=%d "
                "completed_adds=%d completed_removals=%d",
                *changes,
            )
        await self._reconcile_pending_actions()

    async def _refresh_access_panel(self) -> None:
        channel_id = self._settings.panel_channel_id
        message_id = self._settings.panel_message_id
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=access_panel_embed(self._settings.approval_mode),
                view=AccessPanelView(self),
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not refresh access panel: %s", error)

    async def _refresh_admin_panel(self) -> None:
        channel_id = self._settings.admin_panel_channel_id
        message_id = self._settings.admin_panel_message_id
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            message = await channel.fetch_message(message_id)
            await message.edit(embed=admin_panel_embed(), view=AdminPanelView(self))
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not refresh admin panel: %s", error)

    async def _disable_old_panel(self, channel_id: int | None, message_id: int | None) -> None:
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id)
            message = await channel.fetch_message(message_id)
            await message.edit(
                content="このパネルは移動しました。最新のパネルをご利用ください。",
                embed=None,
                view=None,
            )
        except RuntimeError, discord.DiscordException:
            LOGGER.info("Could not disable old panel message %d", message_id)

    async def _save_settings(self, settings: RuntimeSettings) -> None:
        async with self._settings_lock:
            await asyncio.to_thread(self._settings_store.save, settings)
            self._settings = settings

    def _ensure_same_guild(self, guild_id: int) -> None:
        configured = self._settings.guild_id
        if configured is not None and configured != guild_id:
            raise RuntimeError("別のDiscordサーバーには設定できません")

    async def _require_server_manager(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True
            )
            return False
        if not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "この操作には「サーバーの管理」権限が必要です。", ephemeral=True
            )
            return False
        return True

    async def _ensure_tailer_started(self) -> None:
        if self._tailer_task is not None and not self._tailer_task.done():
            return
        await asyncio.to_thread(self._tailer.validate)
        self._delivery_healthy = True
        self._tailer_task = asyncio.create_task(self._forward_logs(), name="minecraft-log-tailer")
        self._tailer_task.add_done_callback(self._tailer_stopped)

    async def _forward_logs(self) -> None:
        async for pending_line in self._tailer.lines():
            event = parse_log_line(pending_line.text)
            if event is None:
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            account = await asyncio.to_thread(self._accounts.find_by_player_name, event.player_name)
            discord_user_id, discord_username = await self._discord_identity(account)
            embed = format_event(event, self._translator, discord_user_id)
            if event.type in {EventType.JOIN, EventType.LEAVE}:
                self._schedule_player_count_refresh()
            retry_delay = 1
            while not self.is_closed():
                await self.wait_until_ready()
                try:
                    await self._send(embed)
                except (RuntimeError, discord.DiscordException) as error:
                    self._delivery_healthy = False
                    LOGGER.warning(
                        "Discord message send failed; retrying in %ds: %s",
                        retry_delay,
                        error,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
                    continue
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                self._delivery_healthy = True
                self._queue_voice_event(event, discord_username)
                break

    def _queue_voice_event(self, event: LogEvent, discord_username: str | None) -> None:
        if not self._settings.voice_enabled or self._settings.guild_id is None:
            return
        text = event_speech_text(
            event,
            self._translator,
            self._config.floodgate_username_prefix,
            discord_username,
        )
        self._voice_player.enqueue(self._settings.guild_id, text)

    async def _discord_identity(
        self, account: MinecraftAccount | None
    ) -> tuple[int | None, str | None]:
        if account is None or account.discord_user_id is None:
            return None, None
        guild = self.get_guild(self._settings.guild_id or 0)
        member = guild.get_member(account.discord_user_id) if guild is not None else None
        if member is not None and member.name != account.discord_username:
            await asyncio.to_thread(self._accounts.update_discord_username, member.id, member.name)
        username = member.name if member is not None else account.discord_username
        return account.discord_user_id, username

    async def _send(self, embed: discord.Embed) -> None:
        if self._channel is None:
            raise RuntimeError("Discord channel has not been validated")
        await self._channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _resolve_and_validate_channel(
        self, channel_id: int, *, require_embeds: bool = False
    ) -> discord.TextChannel:
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Discord channel {channel_id} is not a text channel")
        member = channel.guild.me
        if member is None:
            raise RuntimeError("The bot is not a member of the configured Discord server")
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.send_messages:
            raise RuntimeError("Botに「チャンネルを見る」「メッセージを送信」権限が必要です")
        if require_embeds and not permissions.embed_links:
            raise RuntimeError("Botに「埋め込みリンク」権限が必要です")
        return channel

    async def _connect_voice_channel(self, channel: discord.VoiceChannel) -> bool:
        member = channel.guild.me
        if member is None:
            raise RuntimeError("BotがDiscordサーバーに参加していません")
        permissions = channel.permissions_for(member)
        if not permissions.connect or not permissions.speak:
            raise RuntimeError("BotにVCの「接続」と「発言」権限が必要です")
        voice_client = channel.guild.voice_client
        if voice_client is None or not voice_client.is_connected():
            if voice_client is not None:
                await voice_client.disconnect(force=True)
            await channel.connect(timeout=15, reconnect=True, self_deaf=True)
            return True
        if voice_client.channel.id != channel.id:
            await voice_client.move_to(channel)
            return True
        return False

    async def _restore_voice_connection(self) -> None:
        channel_id = self._settings.voice_channel_id
        if channel_id is None:
            return
        if not self._voice_player.configured:
            LOGGER.warning("Minecraft voice is enabled but VOICEVOX TTS API is not configured")
            return
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                raise RuntimeError("設定済みの読み上げ先がVCではありません")
            connected = await self._connect_voice_channel(channel)
            if connected:
                self._voice_player.enqueue(channel.guild.id, _VOICE_CONNECTED_SPEECH)
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not restore Minecraft voice connection: %s", error)

    async def _health_loop(self) -> None:
        while not self.is_closed():
            unconfigured = self._channel is None
            tailer_running = self._tailer_task is not None and not self._tailer_task.done()
            forwarding_healthy = tailer_running and self._delivery_healthy
            if self.is_ready() and (unconfigured or forwarding_healthy):
                self._health_path.touch()
            else:
                self._remove_health_file()
            await self._resume_whitelist_if_due()
            self._sync_ticks += 1
            if self._sync_ticks >= 6:
                self._sync_ticks = 0
                await self._sync_whitelist_accounts()
                if self._settings.voice_enabled:
                    await self._restore_voice_connection()
            self._schedule_player_count_refresh(delay=0)
            await asyncio.sleep(10)

    async def _enable_player_count_channel(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
    ) -> discord.VoiceChannel:
        self._require_rcon()
        member = guild.me
        if (
            member is None
            or not member.guild_permissions.manage_channels
            or not member.guild_permissions.set_voice_channel_status
        ):
            raise RuntimeError(
                "Botに「チャンネルの管理」と「ボイスチャンネルステータスの設定」権限が必要です"
            )

        channel = await self._get_player_count_channel(guild)
        if channel is None:
            category = getattr(interaction.channel, "category", None)
            if not isinstance(category, discord.CategoryChannel):
                category = None
            channel = await guild.create_voice_channel(
                PLAYER_COUNT_CHANNEL_NAME,
                category=category,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        connect=False,
                        speak=False,
                    )
                },
                reason="Minecraftオンライン人数表示を作成",
            )
        else:
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.connect is not False or overwrite.speak is not False:
                overwrite.connect = False
                overwrite.speak = False
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=overwrite,
                    reason="人数表示チャンネルを閲覧専用に設定",
                )

        updated = replace(
            self._settings,
            guild_id=guild.id,
            player_count_channel_id=channel.id,
            player_count_enabled=True,
        )
        await self._save_settings(updated)
        await self._refresh_player_count_channel(channel)
        self._schedule_player_count_name_normalization()
        return channel

    async def _get_player_count_channel(
        self,
        guild: discord.Guild,
    ) -> discord.VoiceChannel | None:
        channel_id = self._settings.player_count_channel_id
        if channel_id is None:
            return None
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                return None
        return channel if isinstance(channel, discord.VoiceChannel) else None

    async def _refresh_player_count_channel(
        self,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        async with self._player_count_update_lock:
            if not self._settings.player_count_enabled:
                return
            guild = self.get_guild(self._settings.guild_id or 0)
            if channel is None:
                if guild is None:
                    raise RuntimeError("設定したDiscordサーバーを取得できません")
                channel = await self._get_player_count_channel(guild)
            if channel is None:
                raise RuntimeError("オンライン人数チャンネルが見つかりません")

            count: int | None = None
            try:
                response = await asyncio.to_thread(self._require_rcon().execute, "list")
                count = parse_online_player_count(response)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning("Could not read Minecraft online player count: %s", error)

            status = player_count_status(count)
            if status == self._last_player_count_status:
                return
            await channel.edit(
                status=status,
                reason="Minecraftオンライン人数ステータスを更新",
            )
            self._last_player_count_status = status

    async def _refresh_player_count_channel_safely(self) -> None:
        if not self._settings.player_count_enabled:
            return
        try:
            await self._refresh_player_count_channel()
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not update player count channel: %s", error)

    def _schedule_player_count_refresh(self, *, delay: float = 1) -> None:
        if not self._settings.player_count_enabled:
            return
        if self._player_count_task is not None and not self._player_count_task.done():
            return
        self._player_count_task = asyncio.create_task(
            self._refresh_player_count_after_delay(delay),
            name="player-count-refresh",
        )

    async def _refresh_player_count_after_delay(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        await self._refresh_player_count_channel_safely()

    def _schedule_player_count_name_normalization(self) -> None:
        if self._player_count_name_task is not None and not self._player_count_name_task.done():
            return
        self._player_count_name_task = asyncio.create_task(
            self._normalize_player_count_channel_name(),
            name="player-count-name-normalization",
        )

    async def _normalize_player_count_channel_name(self) -> None:
        try:
            guild = self.get_guild(self._settings.guild_id or 0)
            if guild is None:
                return
            channel = await self._get_player_count_channel(guild)
            if channel is not None and channel.name != PLAYER_COUNT_CHANNEL_NAME:
                await channel.edit(
                    name=PLAYER_COUNT_CHANNEL_NAME,
                    reason="Minecraftオンライン人数をボイスチャンネルステータスへ移行",
                )
        except discord.DiscordException as error:
            LOGGER.warning("Could not normalize player count channel name: %s", error)

    async def _reconcile_pending_actions(self) -> None:
        for account in await asyncio.to_thread(self._accounts.list_pending_actions):
            try:
                if account.status == "pending_add":
                    await self._add_to_whitelist(account)
                else:
                    await self._remove_from_whitelist(account)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Minecraft account reconciliation remains pending for %s: %s",
                    account.minecraft_name,
                    error,
                )

    def _tailer_stopped(self, task: asyncio.Task[None]) -> None:
        self._remove_health_file()
        if self._closing or task.cancelled():
            return
        error = task.exception()
        if error is None:
            LOGGER.error("Minecraft log tailer stopped unexpectedly")
        else:
            LOGGER.error(
                "Minecraft log tailer failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _remove_health_file(self) -> None:
        self._health_path.unlink(missing_ok=True)

    @staticmethod
    def _channel_text(channel_id: int | None) -> str:
        return "未設定" if channel_id is None else f"<#{channel_id}> (`{channel_id}`)"

    @staticmethod
    def _account_line(account: MinecraftAccount) -> str:
        edition = "Java版" if account.edition == "java" else "Bedrock版"
        state = {
            "active": "参加可能",
            "pending_approval": "承認待ち",
            "pending_add": "反映待ち",
            "pending_remove": "解除反映待ち",
        }.get(account.status, account.status)
        protection = "・保護" if not account.managed else ""
        name = discord.utils.escape_markdown(account.minecraft_name)
        return f"・**{name}** / {edition} / {state}{protection}"
