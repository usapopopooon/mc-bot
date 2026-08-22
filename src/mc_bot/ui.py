from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from mc_bot.accounts import MinecraftAccount
from mc_bot.admin_quest import AdminQuestCreateModal

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot


def access_panel_embed(approval_mode: str) -> discord.Embed:
    description = (
        "このDiscordサーバーの参加者は、Minecraftで使用している名前を登録できます。\n\n"
        "☕ **Java版**\n"
        "ゲーム内で自分のキャラクターの頭上に表示される名前"
        "、つまりJava版のプレイヤー名を入力してください。\n\n"
        "🪨 **Bedrock版**\n"
        "Switch・Xbox・PlayStation・スマホ・Windowsのゲーム内で、自分のキャラクターの"
        "頭上に表示される名前、つまりXboxゲーマータグを入力してください。"
        "`名前#数字`を入力した場合はゲーム内形式の`名前数字`へ自動変換します。\n\n"
        "複数のMinecraftアカウントを登録できます。"
    )
    if approval_mode == "automatic":
        description += "\n\n登録後、すぐにMinecraftサーバーへ参加できます。"
    else:
        description += "\n\n管理者の承認後、Minecraftサーバーへ参加できます。"
    return discord.Embed(
        title="🎮 Minecraftサーバーに参加",
        description=description,
        color=discord.Color.green(),
    )


def admin_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="🛠 Minecraft管理メニュー",
        description=(
            "代理登録、既存whitelistの紐付け・修正、登録状況の確認に加え、\n"
            "稼働中のMinecraftサーバー操作や、Bot発行クエストの作成ができます。"
        ),
        color=discord.Color.blurple(),
    )


class AccessPanelView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Java版で登録",
        emoji="☕",
        style=discord.ButtonStyle.primary,
        custom_id="mc-access:register:java",
    )
    async def java(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=False):
            await interaction.response.send_modal(RegistrationModal(self.bot, "java"))

    @discord.ui.button(
        label="Bedrock版で登録",
        emoji="🪨",
        style=discord.ButtonStyle.primary,
        custom_id="mc-access:register:bedrock",
    )
    async def bedrock(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=False):
            await interaction.response.send_modal(RegistrationModal(self.bot, "bedrock"))

    @discord.ui.button(
        label="登録内容を確認・変更",
        emoji="👤",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-access:manage",
    )
    async def manage(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=False):
            await self.bot.show_user_accounts(interaction)


class AdminPanelView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="プレイヤーを代理登録",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="mc-admin:proxy-register",
    )
    async def proxy_register(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await interaction.response.send_message(
                "代理登録するDiscordユーザーを選択してください。",
                view=TargetUserView(self.bot, purpose="proxy"),
                ephemeral=True,
            )

    @discord.ui.button(
        label="既存whitelistを紐付け",
        emoji="🔗",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-admin:link-existing",
    )
    async def link_existing(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await self.bot.show_unlinked_accounts(interaction)

    @discord.ui.button(
        label="Discord紐付け先を修正",
        emoji="🛠️",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-admin:relink-existing",
        row=1,
    )
    async def relink_existing(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await self.bot.show_relinkable_accounts(interaction)

    @discord.ui.button(
        label="Minecraft IDを修正",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-admin:correct-minecraft-id",
        row=1,
    )
    async def correct_minecraft_id(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await self.bot.show_pending_removal_corrections(interaction)

    @discord.ui.button(
        label="登録状況",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-admin:summary",
    )
    async def summary(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await self.bot.show_admin_summary(interaction)

    @discord.ui.button(
        label="Whitelist一覧",
        emoji="🛡️",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-admin:whitelist-list",
    )
    async def whitelist_list(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await self.bot.show_whitelist_entries(interaction)

    @discord.ui.button(
        label="サーバー操作",
        emoji="🎮",
        style=discord.ButtonStyle.danger,
        custom_id="mc-admin:server-control",
    )
    async def server_control(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await self.bot.show_server_control(interaction)

    @discord.ui.button(
        label="Minecraft読み上げ",
        emoji="🔊",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-admin:voice",
        row=1,
    )
    async def voice(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await self.bot.show_voice_controls(interaction)

    @discord.ui.button(
        label="Bot発行クエスト",
        emoji="📜",
        style=discord.ButtonStyle.primary,
        custom_id="mc-admin:quest-create",
        row=2,
    )
    async def quest_create(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_panel_interaction(interaction, admin=True):
            await interaction.response.send_modal(AdminQuestCreateModal(self.bot))


class RegistrationModal(discord.ui.Modal):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        edition: str,
        *,
        target: discord.Member | None = None,
        source: str = "self",
    ) -> None:
        title = "Java版アカウントを登録" if edition == "java" else "Bedrock版を登録"
        super().__init__(title=title)
        self.bot = bot
        self.edition = edition
        self.target = target
        self.source = source
        if edition == "java":
            label = "Java版のプレイヤー名"
            placeholder = "例: Steve123"
            max_length = 16
            description = "ゲーム内で自分のキャラクターの頭上に表示される名前"
        else:
            label = "Xboxゲーマータグ"
            placeholder = "Minecraftに表示されるゲーマータグ"
            max_length = 32
            description = (
                "ゲーム内で自分のキャラクターの頭上に表示される名前。"
                "名前#数字はゲーム内形式の名前数字へ自動変換します"
            )
        self.minecraft_name = discord.ui.TextInput(
            placeholder=placeholder,
            min_length=1,
            max_length=max_length,
        )
        self.minecraft_name_label = discord.ui.Label(
            text=label,
            description=description,
            component=self.minecraft_name,
        )
        self.add_item(self.minecraft_name_label)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        target = self.target
        if target is None:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message(
                    "Discordサーバー内で操作してください。", ephemeral=True
                )
                return
            target = interaction.user
        await self.bot.confirm_registration(
            interaction,
            edition=self.edition,
            minecraft_name=str(self.minecraft_name),
            target=target,
            source=self.source,
        )


class ConfirmRegistrationView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        target: discord.Member,
        edition: str,
        minecraft_name: str,
        source: str,
    ) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.owner_id = owner_id
        self.target = target
        self.edition = edition
        self.minecraft_name = minecraft_name
        self.source = source

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("この確認画面は操作できません。", ephemeral=True)
        return False

    @discord.ui.button(label="この内容で登録", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        await self.bot.register_account(
            interaction,
            edition=self.edition,
            minecraft_name=self.minecraft_name,
            target=self.target,
            source=self.source,
        )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="登録をキャンセルしました。", view=None)


class TargetUserSelect(discord.ui.UserSelect):
    def __init__(self, bot: MinecraftDiscordBot, purpose: str, account_id: int | None) -> None:
        super().__init__(placeholder="Discordユーザーを選択", min_values=1, max_values=1)
        self.bot = bot
        self.purpose = purpose
        self.account_id = account_id

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        if not isinstance(selected, discord.Member) or selected.bot:
            await interaction.response.edit_message(
                content="Bot以外のサーバーメンバーを選択してください。", view=None
            )
            return
        if self.purpose == "proxy":
            await interaction.response.edit_message(
                content=f"{selected.mention} に登録するエディションを選択してください。",
                view=EditionView(self.bot, selected),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if self.purpose == "relink":
            await self.bot.confirm_account_relink(
                interaction,
                self.account_id or 0,
                selected,
            )
            return
        await interaction.response.edit_message(
            content=f"{selected.mention} への紐付け方法を選択してください。",
            view=LinkModeView(self.bot, self.account_id or 0, selected),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class TargetUserView(discord.ui.View):
    def __init__(
        self, bot: MinecraftDiscordBot, *, purpose: str, account_id: int | None = None
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(TargetUserSelect(bot, purpose, account_id))


class EditionView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, target: discord.Member) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.target = target

    @discord.ui.button(label="Java版", style=discord.ButtonStyle.primary)
    async def java(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            RegistrationModal(self.bot, "java", target=self.target, source="admin")
        )

    @discord.ui.button(label="Bedrock版", style=discord.ButtonStyle.primary)
    async def bedrock(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            RegistrationModal(self.bot, "bedrock", target=self.target, source="admin")
        )


class AccountSelect(discord.ui.Select):
    def __init__(
        self, bot: MinecraftDiscordBot, accounts: list[MinecraftAccount], purpose: str
    ) -> None:
        options = []
        for account in accounts:
            edition = "Java版" if account.edition == "java" else "Bedrock版"
            description = edition
            if purpose == "relink":
                current = account.discord_username or f"Discord ID: {account.discord_user_id}"
                state = (
                    " / 削除反映待ち"
                    if account.status == "pending_remove"
                    else " / 削除済み"
                    if account.status == "missing"
                    else ""
                )
                description = f"{edition} / 現在: {current}{state}"[:100]
            elif purpose == "correct_id":
                current = account.discord_username or f"Discord ID: {account.discord_user_id}"
                description = f"{edition} / 削除中 / {current}"[:100]
            elif purpose == "remove" and account.status == "pending_remove":
                description = f"{edition} / Whitelist解除を再試行"
            options.append(
                discord.SelectOption(
                    label=account.minecraft_name[:100],
                    description=description,
                    value=str(account.id),
                )
            )
        placeholders = {
            "link": "紐付ける既存アカウントを選択",
            "relink": "紐付け先を修正するアカウントを選択",
            "correct_id": "誤登録したMinecraft IDを選択",
            "remove": "解除・再試行するアカウントを選択",
        }
        placeholder = placeholders.get(purpose, "Minecraftアカウントを選択")
        super().__init__(placeholder=placeholder, options=options)
        self.bot = bot
        self.purpose = purpose
        self.accounts = {account.id: account for account in accounts}

    async def callback(self, interaction: discord.Interaction) -> None:
        account_id = int(self.values[0])
        if self.purpose == "link":
            await interaction.response.edit_message(
                content="紐付け先のDiscordユーザーを選択してください。",
                view=TargetUserView(self.bot, purpose="link", account_id=account_id),
            )
            return
        if self.purpose == "relink":
            await interaction.response.edit_message(
                content="新しい紐付け先のDiscordユーザーを選択してください。",
                view=TargetUserView(self.bot, purpose="relink", account_id=account_id),
            )
            return
        if self.purpose == "correct_id":
            account = self.accounts[account_id]
            await interaction.response.send_modal(
                MinecraftIdCorrectionModal(self.bot, account.id, account.edition)
            )
            return
        account = self.accounts[account_id]
        retrying_removal = account.status == "pending_remove"
        await interaction.response.edit_message(
            content=(
                "このMinecraftアカウントのWhitelist解除を再試行しますか?"
                if retrying_removal
                else "このMinecraftアカウントの参加登録を解除しますか?"
            ),
            view=ConfirmRemovalView(
                self.bot,
                interaction.user.id,
                account_id,
                retrying_removal=retrying_removal,
            ),
        )


class AccountSelectView(discord.ui.View):
    def __init__(
        self, bot: MinecraftDiscordBot, accounts: list[MinecraftAccount], purpose: str
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(AccountSelect(bot, accounts, purpose))


class LinkModeView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, account_id: int, target: discord.Member) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.account_id = account_id
        self.target = target

    @discord.ui.button(label="保護したまま紐付け", style=discord.ButtonStyle.secondary)
    async def protected(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.link_existing_account(
            interaction, self.account_id, self.target, managed=False
        )

    @discord.ui.button(label="自動管理へ移行", style=discord.ButtonStyle.danger)
    async def managed(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.link_existing_account(
            interaction, self.account_id, self.target, managed=True
        )


class ConfirmRelinkView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        account_id: int,
        expected_discord_user_id: int,
        target: discord.Member,
        recover_pending_remove: bool = False,
    ) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.owner_id = owner_id
        self.account_id = account_id
        self.expected_discord_user_id = expected_discord_user_id
        self.target = target
        self.recover_pending_remove = recover_pending_remove

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("この確認画面は操作できません。", ephemeral=True)
        return False

    @discord.ui.button(label="紐付け先を変更", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.reassign_account_link(
            interaction,
            account_id=self.account_id,
            expected_discord_user_id=self.expected_discord_user_id,
            target=self.target,
            recover_pending_remove=self.recover_pending_remove,
        )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="変更をキャンセルしました。", view=None)


class MinecraftIdCorrectionModal(discord.ui.Modal):
    def __init__(self, bot: MinecraftDiscordBot, account_id: int, edition: str) -> None:
        edition_label = "Java版" if edition == "java" else "Bedrock版"
        super().__init__(title=f"{edition_label}のMinecraft IDを修正")
        self.bot = bot
        self.account_id = account_id
        label = "正しいプレイヤー名" if edition == "java" else "正しいXboxゲーマータグ"
        self.correct_name = discord.ui.TextInput(
            placeholder="Minecraft内で実際に表示される名前",
            min_length=1,
            max_length=16 if edition == "java" else 32,
        )
        self.correct_name_label = discord.ui.Label(
            text=label,
            description=(
                "ゲーム内でキャラクターの頭上に表示される名前"
                if edition == "java"
                else "名前#数字はゲーム内形式の名前数字へ自動変換します"
            ),
            component=self.correct_name,
        )
        self.add_item(self.correct_name_label)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.confirm_minecraft_id_correction(
            interaction,
            self.account_id,
            str(self.correct_name),
        )


class ConfirmMinecraftIdCorrectionView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        old_account_id: int,
        expected_discord_user_id: int,
        edition: str,
        minecraft_name: str,
    ) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.owner_id = owner_id
        self.old_account_id = old_account_id
        self.expected_discord_user_id = expected_discord_user_id
        self.edition = edition
        self.minecraft_name = minecraft_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("この確認画面は操作できません。", ephemeral=True)
        return False

    @discord.ui.button(label="正しいIDへ修正", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.correct_pending_removal_minecraft_id(
            interaction,
            old_account_id=self.old_account_id,
            expected_discord_user_id=self.expected_discord_user_id,
            edition=self.edition,
            minecraft_name=self.minecraft_name,
        )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="修正をキャンセルしました。", view=None)


class ConfirmRemovalView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        owner_id: int,
        account_id: int,
        *,
        retrying_removal: bool = False,
    ) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.owner_id = owner_id
        self.account_id = account_id
        if retrying_removal:
            self.confirm.label = "解除を再試行"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("この確認画面は操作できません。", ephemeral=True)
        return False

    @discord.ui.button(label="解除する", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.remove_account(interaction, self.account_id)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="解除をキャンセルしました。", view=None)


class ApprovalButton(discord.ui.Button["ApprovalView"]):
    def __init__(self, account_id: int, *, approved: bool) -> None:
        super().__init__(
            label="承認" if approved else "却下",
            style=(discord.ButtonStyle.success if approved else discord.ButtonStyle.danger),
            custom_id=f"mc-approval:{'approve' if approved else 'reject'}:{account_id}",
        )
        self.approved = approved

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            await self.view.bot.process_approval(
                interaction,
                self.view.account_id,
                approved=self.approved,
            )


class ApprovalView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, account_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.account_id = account_id
        self.add_item(ApprovalButton(account_id, approved=True))
        self.add_item(ApprovalButton(account_id, approved=False))


class AdminOnlyView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, owner_id: int, *, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この管理画面は操作できません。", ephemeral=True
            )
            return False
        return await self.bot.validate_runtime_admin(interaction)


class VoiceChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Minecraft読み上げ先VCを選択",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "通常のボイスチャンネルを選択してください。",
                ephemeral=True,
            )
            return
        if isinstance(self.view, VoiceControlView):
            await self.view.bot.configure_voice_channel(interaction, channel)


class VoiceControlView(AdminOnlyView):
    def __init__(self, bot: MinecraftDiscordBot, owner_id: int) -> None:
        super().__init__(bot, owner_id)
        self.add_item(VoiceChannelSelect())

    @discord.ui.button(label="読み上げ確認", emoji="🔈", style=discord.ButtonStyle.primary, row=1)
    async def test(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.test_voice(interaction)

    @discord.ui.button(label="切断", emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def disconnect(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.disconnect_voice(interaction)


class ServerControlView(AdminOnlyView):
    @discord.ui.button(label="最新状態", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.refresh_server_control(interaction, self)

    @discord.ui.button(label="プレイヤー", emoji="👥", style=discord.ButtonStyle.secondary)
    async def players(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.show_online_players(interaction)

    @discord.ui.button(label="キック", emoji="🚪", style=discord.ButtonStyle.danger)
    async def kick(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.show_kick_player_select(interaction)

    @discord.ui.button(label="サーバー告知", emoji="📢", style=discord.ButtonStyle.secondary)
    async def announce(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AnnouncementModal(self.bot))

    @discord.ui.button(label="Whitelist", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def whitelist(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.show_whitelist_controls(interaction)

    @discord.ui.button(label="天候・時刻", emoji="🌤️", style=discord.ButtonStyle.secondary, row=1)
    async def world(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "変更する天候または時刻を選択してください。",
            view=WorldControlView(self.bot, interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="パフォーマンス", emoji="📊", style=discord.ButtonStyle.secondary, row=1
    )
    async def performance(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.show_performance(interaction)


class KickPlayerSelect(discord.ui.Select):
    def __init__(self, bot: MinecraftDiscordBot, players: list[str]) -> None:
        super().__init__(
            placeholder="キックするプレイヤーを選択",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=player, value=player) for player in players],
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(KickReasonModal(self.bot, self.values[0]))


class KickPlayerSelectView(AdminOnlyView):
    def __init__(self, bot: MinecraftDiscordBot, owner_id: int, players: list[str]) -> None:
        super().__init__(bot, owner_id)
        self.add_item(KickPlayerSelect(bot, players))


class KickReasonModal(discord.ui.Modal, title="プレイヤーをキック"):
    reason = discord.ui.TextInput(
        label="理由",
        default="管理者によりキックされました",
        min_length=1,
        max_length=200,
    )

    def __init__(self, bot: MinecraftDiscordBot, player_name: str) -> None:
        super().__init__()
        self.bot = bot
        self.player_name = player_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"**{discord.utils.escape_markdown(self.player_name)}** をキックしますか?\n"
            f"理由: {discord.utils.escape_markdown(str(self.reason))}",
            view=ConfirmKickView(
                self.bot,
                interaction.user.id,
                self.player_name,
                str(self.reason),
            ),
            ephemeral=True,
        )


class ConfirmKickView(AdminOnlyView):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        owner_id: int,
        player_name: str,
        reason: str,
    ) -> None:
        super().__init__(bot, owner_id, timeout=120)
        self.player_name = player_name
        self.reason = reason

    @discord.ui.button(label="キックする", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.kick_online_player(interaction, self.player_name, self.reason)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="キックをキャンセルしました。", view=None)


class AnnouncementModal(discord.ui.Modal, title="Minecraftサーバーへ告知"):
    message = discord.ui.TextInput(
        label="告知内容",
        placeholder="サーバー内の全プレイヤーへ表示します",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=200,
    )

    def __init__(self, bot: MinecraftDiscordBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.announce_server(interaction, str(self.message))


class WhitelistControlView(AdminOnlyView):
    @discord.ui.button(label="15分停止", style=discord.ButtonStyle.danger)
    async def pause_15(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._confirm_pause(interaction, 15)

    @discord.ui.button(label="30分停止", style=discord.ButtonStyle.danger)
    async def pause_30(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._confirm_pause(interaction, 30)

    @discord.ui.button(label="1時間停止", style=discord.ButtonStyle.danger)
    async def pause_60(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._confirm_pause(interaction, 60)

    @discord.ui.button(label="今すぐ再開", style=discord.ButtonStyle.success)
    async def resume(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.resume_whitelist(interaction)

    async def _confirm_pause(self, interaction: discord.Interaction, minutes: int) -> None:
        await interaction.response.send_message(
            f"⚠️ Whitelistを{minutes}分間停止すると、未登録者も接続できます。\n本当に停止しますか?",
            view=ConfirmWhitelistPauseView(self.bot, interaction.user.id, minutes),
            ephemeral=True,
        )


class ConfirmWhitelistPauseView(AdminOnlyView):
    def __init__(self, bot: MinecraftDiscordBot, owner_id: int, minutes: int) -> None:
        super().__init__(bot, owner_id, timeout=120)
        self.minutes = minutes

    @discord.ui.button(label="Whitelistを停止", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.pause_whitelist(interaction, self.minutes)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Whitelistの停止をキャンセルしました。", view=None
        )


class WorldControlView(AdminOnlyView):
    @discord.ui.button(label="晴れ", emoji="☀️", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.change_world(interaction, "weather clear", "天候を晴れ")

    @discord.ui.button(label="雨", emoji="🌧️", style=discord.ButtonStyle.secondary)
    async def rain(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.change_world(interaction, "weather rain", "天候を雨")

    @discord.ui.button(label="雷雨", emoji="⛈️", style=discord.ButtonStyle.secondary)
    async def thunder(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.change_world(interaction, "weather thunder", "天候を雷雨")

    @discord.ui.button(label="朝", emoji="🌅", style=discord.ButtonStyle.secondary, row=1)
    async def day(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.change_world(interaction, "time set day", "時刻を朝")

    @discord.ui.button(label="夜", emoji="🌙", style=discord.ButtonStyle.secondary, row=1)
    async def night(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.change_world(interaction, "time set night", "時刻を夜")
