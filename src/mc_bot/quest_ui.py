from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import discord

from mc_bot.quest import Quest

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot


class _QuestButton(discord.ui.Button[discord.ui.View]):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        quest_id: int,
        *,
        action: str,
        label: str,
        style: discord.ButtonStyle,
        return_page: int | None = None,
        update_message: bool = False,
    ) -> None:
        super().__init__(
            label=label,
            style=style,
            custom_id=f"mc-quest:{action}:{quest_id}",
        )
        self.bot = bot
        self.quest_id = quest_id
        self.action = action
        self.return_page = return_page
        self.update_message = update_message

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action in {"accept", "cancel", "submit", "abandon"}:
            await self.bot.show_quest_action_confirmation(
                interaction,
                self.quest_id,
                self.action,
                return_page=self.return_page,
            )
        elif self.action == "confirm-accept":
            await self.bot.accept_quest(interaction, self.quest_id)
        elif self.action == "confirm-cancel":
            await self.bot.cancel_quest(interaction, self.quest_id)
        elif self.action == "confirm-submit":
            await self.bot.submit_quest(interaction, self.quest_id)
        elif self.action == "confirm-abandon":
            await self.bot.abandon_quest(interaction, self.quest_id)
        elif self.action == "mine":
            await self.bot.show_my_quests(
                interaction,
                update_message=self.update_message,
            )
        elif self.action == "guide":
            await self.bot.show_quest_guide(
                interaction,
                update_message=self.update_message,
            )
        else:
            await self.bot.show_quest_claim_guide(
                interaction,
                update_message=self.update_message,
            )


class QuestPanelView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        update_message = owner_id is not None
        self.add_item(
            _QuestButton(
                bot,
                0,
                action="guide",
                label="使い方",
                style=discord.ButtonStyle.primary,
                update_message=update_message,
            )
        )
        self.add_item(
            _QuestButton(
                bot,
                0,
                action="mine",
                label="自分のクエスト",
                style=discord.ButtonStyle.secondary,
                update_message=update_message,
            )
        )
        self.add_item(
            _QuestButton(
                bot,
                0,
                action="claim",
                label="受取方法",
                style=discord.ButtonStyle.secondary,
                update_message=update_message,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is None or interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この個人メニューを使えるのは開いた本人だけです。", ephemeral=True
        )
        return False


class QuestListingView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, quest_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(
            _QuestButton(
                bot,
                quest_id,
                action="accept",
                label="受注",
                style=discord.ButtonStyle.primary,
            )
        )
        self.add_item(
            _QuestButton(
                bot,
                quest_id,
                action="cancel",
                label="依頼取消",
                style=discord.ButtonStyle.danger,
            )
        )


def quest_listing_has_current_controls(message: discord.Message, quest_id: int) -> bool:
    expected = {f"mc-quest:accept:{quest_id}", f"mc-quest:cancel:{quest_id}"}
    actual: set[str] = set()
    for row in message.components:
        for component in getattr(row, "children", ()):
            custom_id = getattr(component, "custom_id", None)
            if custom_id is not None:
                actual.add(custom_id)
    return expected.issubset(actual)


class _QuestConfirmationCancelButton(discord.ui.Button[discord.ui.View]):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        quest_id: int,
        *,
        owner_id: int,
        return_page: int | None,
    ) -> None:
        super().__init__(
            label="戻る",
            style=discord.ButtonStyle.secondary,
            custom_id=f"mc-quest:confirmation-cancel:{quest_id}",
        )
        self.bot = bot
        self.owner_id = owner_id
        self.return_page = return_page

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.return_page is not None:
            await self.bot.show_my_quests(
                interaction,
                page=self.return_page,
                update_message=True,
            )
            return
        await interaction.response.edit_message(
            content=None,
            embed=quest_panel_embed(),
            view=QuestPanelView(
                self.bot,
                owner_id=self.owner_id,
                timeout=180,
            ),
        )


class QuestActionConfirmationView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        quest: Quest,
        *,
        owner_id: int,
        action: str,
        return_page: int | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        labels = {
            "accept": ("受注を確定", discord.ButtonStyle.primary),
            "cancel": ("取消を確定", discord.ButtonStyle.danger),
            "submit": ("納品を確定", discord.ButtonStyle.success),
            "abandon": ("辞退を確定", discord.ButtonStyle.danger),
        }
        label, style = labels[action]
        self.add_item(
            _QuestButton(
                bot,
                quest.quest_id,
                action=f"confirm-{action}",
                label=label,
                style=style,
            )
        )
        self.add_item(
            _QuestConfirmationCancelButton(
                bot,
                quest.quest_id,
                owner_id=owner_id,
                return_page=return_page,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この確認画面を使えるのは開いた本人だけです。", ephemeral=True
        )
        return False


class _QuestMinePageButton(discord.ui.Button[discord.ui.View]):
    def __init__(self, bot: MinecraftDiscordBot, *, page: int, label: str) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"mc-quest:mine-page:{page}",
            row=1,
        )
        self.bot = bot
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.show_my_quests(interaction, page=self.page, update_message=True)


class _QuestBackButton(discord.ui.Button[discord.ui.View]):
    def __init__(self, bot: MinecraftDiscordBot, owner_id: int) -> None:
        super().__init__(
            label="戻る",
            style=discord.ButtonStyle.secondary,
            custom_id="mc-quest:back:0",
            row=1,
        )
        self.bot = bot
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content=None,
            embed=quest_panel_embed(),
            view=QuestPanelView(
                self.bot,
                owner_id=self.owner_id,
                timeout=180,
            ),
        )


class QuestBackView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.add_item(_QuestBackButton(bot, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この操作画面を使えるのは開いた本人だけです。", ephemeral=True
        )
        return False


class QuestMineView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        quest: Quest,
        owner_id: int,
        *,
        page: int = 0,
        total: int = 1,
    ) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        if quest.owner_discord_user_id == owner_id and quest.status == "open":
            self.add_item(
                _QuestButton(
                    bot,
                    quest.quest_id,
                    action="cancel",
                    label="依頼取消",
                    style=discord.ButtonStyle.danger,
                    return_page=page,
                )
            )
        elif quest.worker_discord_user_id == owner_id and quest.status == "accepted":
            self.add_item(
                _QuestButton(
                    bot,
                    quest.quest_id,
                    action="submit",
                    label="納品",
                    style=discord.ButtonStyle.success,
                    return_page=page,
                )
            )
            self.add_item(
                _QuestButton(
                    bot,
                    quest.quest_id,
                    action="abandon",
                    label="辞退",
                    style=discord.ButtonStyle.danger,
                    return_page=page,
                )
            )
        if page > 0:
            self.add_item(_QuestMinePageButton(bot, page=page - 1, label="前へ"))
        if page + 1 < total:
            self.add_item(_QuestMinePageButton(bot, page=page + 1, label="次へ"))
        self.add_item(_QuestBackButton(bot, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この操作画面を使えるのは開いた本人だけです。", ephemeral=True
        )
        return False


def quest_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="📜 Minecraft ギルド・クエスト掲示板",
        description=(
            "プレイヤー同士でアイテム納品を依頼できます。\n"
            "現在募集中の依頼だけが、このチャンネルにカードとして表示されます。"
        ),
        color=discord.Color.blue(),
    )


def quest_guide_embed() -> discord.Embed:
    embed = discord.Embed(
        title="ギルド・クエストの使い方",
        description=("依頼者が報酬を先に預け、受注者が期限内に依頼品を一括納品する仕組みです。"),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="依頼を作る",
        value=(
            "Minecraftで依頼品を手に持ち `/quest create <個数> <期限時間>`、"
            "次に報酬スタックを持って `/quest confirm` を実行します。"
        ),
        inline=False,
    )
    embed.add_field(
        name="受注・納品",
        value=(
            "このチャンネルの「受注」か `/quest accept <番号>` を使います。"
            "Discordから操作する場合も、連携したMinecraftアカウントで参加してください。"
            "依頼品をメインハンドにまとめ、`/quest submit <番号>` または"
            "「自分のクエスト」の「納品」を使います。"
        ),
        inline=False,
    )
    embed.add_field(
        name="安全な受け取り",
        value=(
            "報酬と納品物は直接インベントリへ押し込まず、永続受取箱へ入ります。"
            "Minecraftで `/quest claim` を実行してください。"
        ),
        inline=False,
    )
    embed.add_field(
        name="制限",
        value=(
            "初期版は名前・エンチャント等のない通常のスタック可能アイテム、1スタック以内、"
            "期限1〜72時間です。受注後は依頼者から取り消せません。"
        ),
        inline=False,
    )
    return embed


def quest_listing_embed(quest: Quest) -> discord.Embed:
    embed = discord.Embed(
        title=f"#{quest.quest_id} {quest.display_requested_item_name} x{quest.requested_count}",
        description=f"報酬: **{quest.display_reward_item_name} x{quest.reward_count}**",
        color=discord.Color.green(),
    )
    owner = (
        f"<@{quest.owner_discord_user_id}>"
        if quest.owner_discord_user_id is not None
        else quest.owner_name
    )
    embed.add_field(name="依頼者", value=owner)
    embed.add_field(name="受注後の期限", value=f"{quest.fulfillment_hours}時間")
    embed.add_field(name="募集終了", value=_relative_time(quest.open_expires_at))
    embed.set_footer(text="受注するとカードは掲示板から消えます")
    return embed


def quest_action_confirmation_embed(quest: Quest, action: str) -> discord.Embed:
    descriptions = {
        "accept": (
            f"**{quest.display_requested_item_name} x{quest.requested_count}** を受注します。\n"
            f"報酬: **{quest.display_reward_item_name} x{quest.reward_count}**\n"
            f"納品期限: 受注から **{quest.fulfillment_hours}時間**\n\n"
            "確定後、募集カードは掲示板から消えます。"
        ),
        "cancel": (
            f"依頼 **#{quest.quest_id} {quest.display_requested_item_name} "
            f"x{quest.requested_count}** を取り消します。\n"
            f"報酬 **{quest.display_reward_item_name} x{quest.reward_count}** は"
            "Minecraftの受取箱へ戻ります。"
        ),
        "submit": (
            f"Minecraftのメインハンドから **{quest.display_requested_item_name} "
            f"x{quest.requested_count}** を納品します。\n"
            f"報酬 **{quest.display_reward_item_name} x{quest.reward_count}** は受取箱へ入ります。"
        ),
        "abandon": (
            f"クエスト **#{quest.quest_id} {quest.display_requested_item_name} "
            f"x{quest.requested_count}** を辞退します。\n依頼は掲示板で再募集されます。"
        ),
    }
    titles = {
        "accept": "受注の最終確認",
        "cancel": "依頼取消の最終確認",
        "submit": "納品の最終確認",
        "abandon": "辞退の最終確認",
    }
    return discord.Embed(
        title=titles[action],
        description=descriptions[action],
        color=discord.Color.orange(),
    )


def quest_mine_embed(
    quest: Quest, user_id: int, *, page: int | None = None, total: int | None = None
) -> discord.Embed:
    if quest.owner_discord_user_id == user_id:
        role = "依頼中"
        state = "募集中" if quest.status == "open" else f"{quest.worker_name} が受注中"
    else:
        role = "受注中"
        state = "依頼品をMinecraftで一括納品してください"
    embed = discord.Embed(
        title=f"{role} #{quest.quest_id}",
        description=(
            f"{quest.display_requested_item_name} x{quest.requested_count} → "
            f"{quest.display_reward_item_name} x{quest.reward_count}\n{state}"
        ),
        color=discord.Color.blue(),
    )
    if quest.status == "accepted" and quest.accepted_deadline is not None:
        embed.add_field(name="納品期限", value=_relative_time(quest.accepted_deadline))
    if page is not None and total is not None:
        embed.set_footer(text=f"{page + 1} / {total}")
    return embed


def quest_log_embed(quest: Quest) -> discord.Embed:
    if quest.status == "completed":
        owner = _mention_or_name(quest.owner_discord_user_id, quest.owner_name)
        worker = _mention_or_name(quest.worker_discord_user_id, quest.worker_name or "不明")
        description = (
            f"{worker} が **{quest.display_requested_item_name} "
            f"x{quest.requested_count}** を納品し、{owner} から "
            f"**{quest.display_reward_item_name} x{quest.reward_count}** を獲得しました。"
        )
        title = f"✅ クエスト #{quest.quest_id} 達成"
        color = discord.Color.green()
    else:
        reason = {
            "expired": "募集期限切れ",
            "invalidated": "Discord連携が確認できず終了",
        }.get(quest.last_transition_kind, "依頼者が取消")
        description = (
            f"依頼: **{quest.display_requested_item_name} x{quest.requested_count}** / "
            f"報酬: **{quest.display_reward_item_name} x{quest.reward_count}**\n理由: {reason}"
        )
        title = f"🗑️ クエスト #{quest.quest_id} 終了"
        color = discord.Color.dark_grey()
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(
        text=(
            "アイテムはMinecraftの /quest claim で受け取れます"
            f" • 記録ID: {quest.last_transition_id}"
        )
    )
    return embed


def _mention_or_name(user_id: int | None, name: str) -> str:
    return f"<@{user_id}>" if user_id is not None else name


def _relative_time(value: str) -> str:
    return f"<t:{int(datetime.fromisoformat(value).timestamp())}:R>"


__all__ = [
    "QuestActionConfirmationView",
    "QuestListingView",
    "QuestMineView",
    "QuestPanelView",
    "quest_action_confirmation_embed",
    "quest_guide_embed",
    "quest_listing_embed",
    "quest_listing_has_current_controls",
    "quest_log_embed",
    "quest_mine_embed",
    "quest_panel_embed",
]
