from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from pathlib import Path

import discord
from discord import app_commands

from mc_bot.accounts import AccountStore, MinecraftAccount
from mc_bot.config import Config
from mc_bot.events import parse_log_line
from mc_bot.formatting import format_event
from mc_bot.rcon import RconClient, RconError
from mc_bot.settings import RuntimeSettings, SettingsStore
from mc_bot.tailer import LogTailer
from mc_bot.translations import AdvancementTranslator
from mc_bot.ui import (
    AccessPanelView,
    AccountSelectView,
    AdminPanelView,
    ApprovalView,
    ConfirmRegistrationView,
    access_panel_embed,
    admin_panel_embed,
)

LOGGER = logging.getLogger(__name__)
_JAVA_NAME = re.compile(r"[A-Za-z0-9_]{3,16}")


class MinecraftDiscordBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

        self._config = config
        self._translator = AdvancementTranslator.load()
        self._tailer = LogTailer(config.minecraft_log_path, config.cursor_path)
        self._settings_store = SettingsStore(config.settings_path)
        self._accounts = AccountStore(config.accounts_path)
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
            name="show",
            description="現在のBot設定と稼働状態を表示します",
        )(self._show_configuration)
        self.tree.add_command(group)

    async def setup_hook(self) -> None:
        await asyncio.to_thread(self._accounts.initialize)
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
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_loop(), name="health-monitor")

        await self._import_whitelist()
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
            except (OSError, RconError, RuntimeError) as error:
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
        if self._tailer_task is not None:
            self._tailer_task.cancel()
            await asyncio.gather(self._tailer_task, return_exceptions=True)
            self._tailer_task = None
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

    async def _show_configuration(self, interaction: discord.Interaction) -> None:
        if not await self._require_server_manager(interaction):
            return
        forwarding = (
            self._channel is not None
            and self._tailer_task is not None
            and not self._tailer_task.done()
            and self._delivery_healthy
        )
        active, unlinked, pending = await asyncio.to_thread(self._accounts.count_summary)
        mode = "自動承認" if self._settings.approval_mode == "automatic" else "管理者承認"
        await interaction.response.send_message(
            "\n".join(
                (
                    f"ログ通知先: {self._channel_text(self._settings.channel_id)}",
                    f"参加パネル: {self._channel_text(self._settings.panel_channel_id)}",
                    f"管理パネル: {self._channel_text(self._settings.admin_panel_channel_id)}",
                    f"承認方式: {mode}",
                    f"申請確認先: {self._channel_text(self._settings.approval_channel_id)}",
                    f"ログ転送: {'稼働中' if forwarding else '停止中'}",
                    f"登録: {active}件 (未連携 {unlinked}件、承認待ち {pending}件)",
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
        except (OSError, RconError, RuntimeError) as error:
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
        if account.status in {"pending_approval", "pending_add"}:
            await asyncio.to_thread(self._accounts.delete_pending, account.id)
            await interaction.response.edit_message(content="申請を取り消しました。", view=None)
            return
        await interaction.response.defer()
        try:
            await self._remove_from_whitelist(account)
        except (OSError, RconError, RuntimeError) as error:
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
        except (OSError, RconError, RuntimeError) as error:
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
        active, unlinked, pending = await asyncio.to_thread(self._accounts.count_summary)
        await interaction.response.send_message(
            f"有効なwhitelist: **{active}件**\n"
            f"未連携・保護: **{unlinked}件**\n"
            f"承認待ち: **{pending}件**",
            ephemeral=True,
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
        rcon = self._require_rcon()
        command = (
            f"whitelist add {account.minecraft_name}"
            if account.edition == "java"
            else f'fwhitelist add "{account.minecraft_name}"'
        )
        await asyncio.to_thread(rcon.execute, command)
        await asyncio.to_thread(self._accounts.update_status, account.id, "active")

    async def _remove_from_whitelist(self, account: MinecraftAccount) -> None:
        rcon = self._require_rcon()
        command = (
            f"whitelist remove {account.minecraft_name}"
            if account.edition == "java"
            else f'fwhitelist remove "{account.minecraft_name}"'
        )
        await asyncio.to_thread(rcon.execute, command)
        try:
            await asyncio.to_thread(
                rcon.execute,
                f'kick "{account.server_player_name}" Discordの参加登録が解除されました',
            )
        except RconError:
            LOGGER.debug("Could not kick %s; player may be offline", account.server_player_name)
        await asyncio.to_thread(self._accounts.update_status, account.id, "missing")

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
        except discord.DiscordException as error:
            LOGGER.warning("Could not refresh access panel: %s", error)

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
            discord_username = await self._discord_username(account)
            embed = format_event(event, self._translator, discord_username)
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
                break

    async def _discord_username(self, account: MinecraftAccount | None) -> str | None:
        if account is None or account.discord_user_id is None:
            return None
        guild = self.get_guild(self._settings.guild_id or 0)
        member = guild.get_member(account.discord_user_id) if guild is not None else None
        if member is not None:
            if member.name != account.discord_username:
                await asyncio.to_thread(
                    self._accounts.update_discord_username, member.id, member.name
                )
            return member.name
        return account.discord_username

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

    async def _health_loop(self) -> None:
        while not self.is_closed():
            unconfigured = self._channel is None
            tailer_running = self._tailer_task is not None and not self._tailer_task.done()
            forwarding_healthy = tailer_running and self._delivery_healthy
            if self.is_ready() and (unconfigured or forwarding_healthy):
                self._health_path.touch()
            else:
                self._remove_health_file()
            self._sync_ticks += 1
            if self._sync_ticks >= 6:
                self._sync_ticks = 0
                await self._import_whitelist()
                await self._reconcile_pending_actions()
            await asyncio.sleep(10)

    async def _reconcile_pending_actions(self) -> None:
        for account in await asyncio.to_thread(self._accounts.list_pending_actions):
            try:
                if account.status == "pending_add":
                    await self._add_to_whitelist(account)
                else:
                    await self._remove_from_whitelist(account)
            except (OSError, RconError, RuntimeError) as error:
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
