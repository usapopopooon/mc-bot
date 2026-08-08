from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING
from uuid import uuid4

import discord

from mc_bot.experience import MinecraftResourcePack, MinecraftResourceShop
from mc_bot.xp_shop import wallet_text

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot

_RESOURCE_NAMES = {
    "minecraft:diamond": "ダイヤモンド",
    "minecraft:emerald": "エメラルド",
}
_SAFE_PLAYER_NAME = re.compile(r"\.?[A-Za-z0-9_]{1,32}")


def resource_give_command(player_name: str, item_id: str, item_count: int) -> str:
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
    if item_id not in _RESOURCE_NAMES:
        raise ValueError("resource item is not allowed")
    if not 1 <= item_count <= 64:
        raise ValueError("item_count must be between 1 and 64")
    return f"give {player_name} {item_id} {item_count}"


def resource_exchange_actionbar_command(
    player_name: str, item_id: str, item_count: int, cost_xp: int
) -> str:
    resource_give_command(player_name, item_id, item_count)
    if cost_xp <= 0:
        raise ValueError("cost_xp must be positive")
    component = {
        "text": f"交換完了: {_RESOURCE_NAMES[item_id]} x{item_count} ({cost_xp} XP)",
        "color": "aqua",
        "bold": True,
    }
    return (
        f"title {player_name} actionbar "
        f"{json.dumps(component, ensure_ascii=False, separators=(',', ':'))}"
    )


def resource_exchange_tellraw_command(
    server_name: str,
    player_name: str,
    item_id: str,
    item_count: int,
    cost_xp: int,
) -> str:
    resource_give_command(player_name, item_id, item_count)
    if cost_xp <= 0:
        raise ValueError("cost_xp must be positive")
    components = [
        {"text": "["},
        {"text": server_name, "color": "aqua"},
        {"text": "] "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんがサーバーXP "},
        {"text": str(cost_xp), "color": "green", "bold": True},
        {"text": "を交換し、"},
        {
            "text": f"{_RESOURCE_NAMES[item_id]} x{item_count}",
            "color": "aqua",
            "bold": True,
        },
        {"text": "を獲得しました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def minecraft_resource_shop_embed(
    packs: tuple[MinecraftResourcePack, ...],
) -> discord.Embed:
    embed = discord.Embed(
        title="Minecraft 資源交換所",
        description=(
            "活動で貯めたサーバーXPをMinecraft内の資源へ交換できます。\n"
            "連携したMinecraftアカウントでサーバーに参加中のみ交換できます。"
        ),
        color=discord.Color.teal(),
    )
    embed.add_field(
        name="交換内容",
        value="\n".join(
            f"`サーバーXP {pack.cost_xp:,}` → `{pack.item_name} x{pack.item_count:,}`"
            for pack in packs
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ 交換前にご確認ください",
        value=(
            "交換時は連携アカウントでMinecraftサーバーに参加してください。\n"
            "インベントリに入りきらない分は、プレイヤーの足元へドロップします。"
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


class MinecraftResourceShopPanelView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="資源を交換",
        emoji="💎",
        style=discord.ButtonStyle.primary,
        custom_id="mc-resource-shop:open",
    )
    async def open_shop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_resource_shop_panel(interaction):
            await self.bot.show_minecraft_resource_shop(interaction)

    @discord.ui.button(
        label="自分のXP",
        style=discord.ButtonStyle.secondary,
        custom_id="mc-resource-shop:balance",
    )
    async def balance(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_resource_shop_panel(interaction):
            await self.bot.show_minecraft_resource_balance(interaction)


class MinecraftResourcePackSelect(discord.ui.Select):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        shop: MinecraftResourceShop,
    ) -> None:
        self.bot = bot
        self.owner_id = owner_id
        self.shop = shop
        super().__init__(
            placeholder="交換する資源を選択",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(f"{pack.item_name} x{pack.item_count:,} (サーバーXP {pack.cost_xp:,})"),
                    value=str(index),
                )
                for index, pack in enumerate(shop.packs)
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この交換メニューを使えるのは開いた本人だけです。", ephemeral=True
            )
            return
        try:
            pack = self.shop.packs[int(self.values[0])]
        except IndexError, ValueError:
            await interaction.response.send_message(
                "この交換内容は利用できません。", ephemeral=True
            )
            return
        affordable = self.shop.wallet.available_xp >= pack.cost_xp
        embed = discord.Embed(
            title="交換内容の確認",
            description=(
                f"サーバーXP **{pack.cost_xp:,}** を使い、"
                f"**{pack.item_name} x{pack.item_count:,}** を獲得します。\n"
                f"現在の交換可能XP: **{self.shop.wallet.available_xp:,} XP**"
            ),
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="交換後",
            value=(
                f"{self.shop.wallet.available_xp - pack.cost_xp:,} XP"
                if affordable
                else "XPが不足しています"
            ),
        )
        embed.add_field(
            name="受け取り",
            value="空きがない場合、入りきらない分は足元へドロップします。",
            inline=False,
        )
        await interaction.response.send_message(
            embed=embed,
            view=MinecraftResourceConfirmView(
                self.bot,
                owner_id=self.owner_id,
                request_id=str(uuid4()),
                pack=pack,
                affordable=affordable,
            ),
            ephemeral=True,
        )


class MinecraftResourcePackSelectView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        shop: MinecraftResourceShop,
    ) -> None:
        super().__init__(timeout=180)
        self.add_item(MinecraftResourcePackSelect(bot, owner_id=owner_id, shop=shop))


class MinecraftResourceConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        request_id: str,
        pack: MinecraftResourcePack,
        affordable: bool,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id
        self.request_id = request_id
        self.pack = pack
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
            result = await self.bot.confirm_minecraft_resource_exchange(
                interaction,
                request_id=self.request_id,
                item_id=self.pack.item_id,
                item_count=self.pack.item_count,
                expected_cost_xp=self.pack.cost_xp,
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


__all__ = [
    "MinecraftResourceConfirmView",
    "MinecraftResourcePackSelectView",
    "MinecraftResourceShopPanelView",
    "minecraft_resource_shop_embed",
    "resource_exchange_actionbar_command",
    "resource_exchange_tellraw_command",
    "resource_give_command",
    "wallet_text",
]
