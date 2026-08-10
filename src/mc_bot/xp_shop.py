from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import discord

from mc_bot.experience import MinecraftXpPack, MinecraftXpShop, MinecraftXpWallet

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot

MINECRAFT_NEAR_LEVEL_50_XP = 5_000


def minecraft_xp_pack_note(pack: MinecraftXpPack) -> str:
    if pack.reward_xp == MINECRAFT_NEAR_LEVEL_50_XP:
        return " (Lv.0からLv.50近く)"
    return ""


def minecraft_xp_pack_text(pack: MinecraftXpPack) -> str:
    return f"Minecraft {pack.reward_xp:,} XP{minecraft_xp_pack_note(pack)}"


def minecraft_xp_shop_embed(packs: tuple[MinecraftXpPack, ...]) -> discord.Embed:
    embed = discord.Embed(
        title="Minecraft XP交換所",
        description=(
            "活動で貯めたサーバーXPをMinecraft内のXPポイントへ交換できます。\n"
            "連携したMinecraftアカウントでサーバーに参加中のみ交換できます。"
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="交換内容",
        value="\n".join(
            f"`サーバーXP {pack.cost_xp:,}` → `{minecraft_xp_pack_text(pack)}`" for pack in packs
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ 交換前にご確認ください",
        value=(
            "交換するときは、連携したアカウントでMinecraftサーバーに"
            "参加している必要があります。\n"
            "**参加していない状態ではMinecraft XPは加算されません。**"
            "その場合、サーバーXPも消費されません。"
        ),
        inline=False,
    )
    embed.add_field(
        name="📢 交換完了時の通知",
        value=(
            "交換が完了すると、交換したことが**Discordのログチャンネル**と"
            "**Minecraft内チャット**に通知されます。"
        ),
        inline=False,
    )
    embed.set_footer(text="残高・選択・確認画面は本人にのみ表示されます")
    return embed


def wallet_text(wallet: MinecraftXpWallet) -> str:
    return (
        f"獲得XP: **{wallet.total_xp:,} XP**\n"
        f"消費済み: **{wallet.spent_xp:,} XP**\n"
        f"現在XP: **{wallet.available_xp:,} XP**"
    )


class MinecraftXpShopPanelView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="XPを交換",
        emoji="⛏️",
        style=discord.ButtonStyle.primary,
        custom_id="mc-xp-shop:open",
    )
    async def open_shop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_xp_shop_panel(interaction):
            await self.bot.show_minecraft_xp_shop(interaction)

    @discord.ui.button(
        label="自分のXP",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-xp-shop:balance",
    )
    async def balance(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_xp_shop_panel(interaction):
            await self.bot.show_minecraft_xp_balance(interaction)


class MinecraftXpPackSelect(discord.ui.Select):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        shop: MinecraftXpShop,
    ) -> None:
        self.bot = bot
        self.owner_id = owner_id
        self.shop = shop
        super().__init__(
            placeholder="交換するXPを選択",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(f"サーバーXP {pack.cost_xp:,} → {minecraft_xp_pack_text(pack)}"),
                    value=str(pack.cost_xp),
                )
                for pack in shop.packs
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この交換メニューを使えるのは開いた本人だけです。",
                ephemeral=True,
            )
            return
        cost_xp = int(self.values[0])
        pack = next(
            (pack for pack in self.shop.packs if pack.cost_xp == cost_xp),
            None,
        )
        if pack is None:
            await interaction.response.send_message(
                "この交換内容は利用できません。", ephemeral=True
            )
            return
        affordable = self.shop.wallet.available_xp >= pack.cost_xp
        embed = discord.Embed(
            title="交換内容の確認",
            description=(
                f"サーバーXP **{pack.cost_xp:,}** を使い、Minecraft内の "
                f"**{pack.reward_xp:,} XP**を獲得します。"
                f"{minecraft_xp_pack_note(pack)}\n"
                f"現在の交換可能XP: **{self.shop.wallet.available_xp:,} XP**"
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="交換後",
            value=(
                f"{self.shop.wallet.available_xp - pack.cost_xp:,} XP"
                if affordable
                else "XPが不足しています"
            ),
        )
        await interaction.response.send_message(
            embed=embed,
            view=MinecraftXpConfirmView(
                self.bot,
                owner_id=self.owner_id,
                request_id=str(uuid4()),
                cost_xp=pack.cost_xp,
                expected_reward_xp=pack.reward_xp,
                affordable=affordable,
            ),
            ephemeral=True,
        )


class MinecraftXpPackSelectView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        shop: MinecraftXpShop,
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(MinecraftXpPackSelect(bot, owner_id=owner_id, shop=shop))


class MinecraftXpConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        request_id: str,
        cost_xp: int,
        expected_reward_xp: int,
        affordable: bool,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id
        self.request_id = request_id
        self.cost_xp = cost_xp
        self.expected_reward_xp = expected_reward_xp
        self._operation_lock = asyncio.Lock()
        self._completed = False
        if not affordable:
            self.confirm.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この交換を操作できるのは本人だけです。", ephemeral=True
        )
        return False

    @discord.ui.button(label="交換する", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self._operation_lock.locked() or self._completed:
            await interaction.response.send_message(
                "この交換は処理中または処理済みです。", ephemeral=True
            )
            return
        async with self._operation_lock:
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
            result = await self.bot.confirm_minecraft_xp_exchange(
                interaction,
                request_id=self.request_id,
                cost_xp=self.cost_xp,
                expected_reward_xp=self.expected_reward_xp,
            )
            if result is None:
                self._enable_retry()
                await interaction.edit_original_response(view=self)
                await interaction.followup.send(
                    "交換結果を確認できませんでした。"
                    "同じ確認画面から再試行しても二重交換にはなりません。",
                    ephemeral=True,
                )
                return
            if result.status == "reserved":
                self._completed = True
            elif result.status in {"offline", "insufficient_xp"}:
                self._enable_retry()
                await interaction.edit_original_response(view=self)
            await interaction.followup.send(result.message, ephemeral=True)

    def _enable_retry(self) -> None:
        self.confirm.disabled = False
        self.cancel.disabled = False

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self._operation_lock.locked():
            await interaction.response.send_message(
                "交換処理中のためキャンセルできません。", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            content="交換をキャンセルしました。", embed=None, view=None
        )
