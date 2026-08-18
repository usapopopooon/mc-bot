from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import discord

from mc_bot.market import MarketListing

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot
    from mc_bot.experience import MinecraftXpWallet


class _MarketButton(discord.ui.Button[discord.ui.View]):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        listing_id: int,
        *,
        action: str,
        label: str,
        style: discord.ButtonStyle,
    ) -> None:
        super().__init__(
            label=label,
            style=style,
            custom_id=f"mc-market:{action}:{listing_id}",
        )
        self.bot = bot
        self.listing_id = listing_id
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action == "buy":
            await self.bot.show_market_purchase_confirmation(interaction, self.listing_id)
        elif self.action == "cancel":
            await self.bot.cancel_market_listing(interaction, self.listing_id)
        else:
            await self.bot.show_market_balance(interaction)


class MarketListingView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, listing_id: int, *, active: bool = True) -> None:
        super().__init__(timeout=None)
        buy = _MarketButton(
            bot,
            listing_id,
            action="buy",
            label="購入",
            style=discord.ButtonStyle.primary,
        )
        cancel = _MarketButton(
            bot,
            listing_id,
            action="cancel",
            label="出品取消",
            style=discord.ButtonStyle.danger,
        )
        balance = _MarketButton(
            bot,
            listing_id,
            action="balance",
            label="自分のXP",
            style=discord.ButtonStyle.secondary,
        )
        buy.disabled = not active
        cancel.disabled = not active
        self.add_item(buy)
        self.add_item(cancel)
        self.add_item(balance)


class MarketPurchaseConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        listing: MarketListing,
        buyer_account_id: int,
        owner_id: int,
        wallet: MinecraftXpWallet,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.listing = listing
        self.buyer_account_id = buyer_account_id
        self.owner_id = owner_id
        self.request_id = str(uuid4())
        self._operation_lock = asyncio.Lock()
        self._completed = False
        if wallet.available_xp < listing.price_xp:
            self.confirm.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この購入確認を操作できるのは開いた本人だけです。", ephemeral=True
        )
        return False

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self._operation_lock.locked() or self._completed:
            await interaction.response.send_message(
                "この購入は処理中または処理済みです。", ephemeral=True
            )
            return
        async with self._operation_lock:
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
            message = await self.bot.purchase_market_listing(
                interaction,
                listing_id=self.listing.listing_id,
                request_id=self.request_id,
                buyer_account_id=self.buyer_account_id,
            )
            if message is None:
                self.confirm.disabled = False
                self.cancel.disabled = False
                await interaction.edit_original_response(view=self)
                await interaction.followup.send(
                    "購入結果を確認できませんでした。同じ画面から再試行できます。",
                    ephemeral=True,
                )
                return
            self._completed = True
            await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="やめる", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)


def market_purchase_confirmation_embed(
    listing: MarketListing, wallet: MinecraftXpWallet
) -> discord.Embed:
    affordable = wallet.available_xp >= listing.price_xp
    embed = discord.Embed(
        title="購入内容の確認",
        description=(
            f"**#{listing.listing_id} {listing.item_name} x{listing.item_count}**\n"
            f"価格: **{listing.price_xp:,} XP**\n"
            f"現在XP: **{wallet.available_xp:,} XP**"
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="購入後",
        value=(
            f"{wallet.available_xp - listing.price_xp:,} XP" if affordable else "XPが不足しています"
        ),
    )
    embed.add_field(
        name="受け取り",
        value="現在オンラインの連携Minecraftアカウントへ届きます。",
        inline=False,
    )
    return embed


def market_balance_text(wallet: MinecraftXpWallet) -> str:
    return (
        f"獲得・売上XP: **{wallet.total_xp:,} XP**\n"
        f"使用済み・予約中: **{wallet.spent_xp:,} XP**\n"
        f"現在XP: **{wallet.available_xp:,} XP**"
    )
