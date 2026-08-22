from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from uuid import uuid4

import discord

from mc_bot.experience import MinecraftResourcePack, MinecraftResourceShop
from mc_bot.player_names import is_safe_server_player_name
from mc_bot.resource_catalog import is_valid_resource_item_id
from mc_bot.xp_shop import wallet_text

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot

_RESOURCE_EMOJIS = {
    "minecraft:diamond": "💎",
    "minecraft:emerald": "🟢",
    "minecraft:gunpowder": "🧨",
}
EMERALD_DIAMOND_PACKS = ((32, 1), (64, 2))
DIAMOND_EMERALD_PACKS = ((1, 16), (4, 64))


def resource_give_command(player_name: str, item_id: str, item_count: int) -> str:
    if not is_safe_server_player_name(player_name):
        raise ValueError("player_name contains unsafe RCON characters")
    if not is_valid_resource_item_id(item_id):
        raise ValueError("resource item is invalid")
    if not 1 <= item_count <= 64:
        raise ValueError("item_count must be between 1 and 64")
    return f"give {player_name} {item_id} {item_count}"


def resource_exchange_actionbar_command(
    player_name: str,
    item_id: str,
    item_name: str,
    item_count: int,
    cost_xp: int,
) -> str:
    resource_give_command(player_name, item_id, item_count)
    _validate_resource_label_and_cost(item_name, cost_xp)
    component = {
        "text": f"交換完了: {item_name} x{item_count} ({cost_xp} XP)",
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
    item_name: str,
    item_count: int,
    cost_xp: int,
) -> str:
    resource_give_command(player_name, item_id, item_count)
    _validate_resource_label_and_cost(item_name, cost_xp)
    components = [
        {"text": "["},
        {"text": server_name, "color": "aqua"},
        {"text": "] "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんがサーバーXP "},
        {"text": str(cost_xp), "color": "green", "bold": True},
        {"text": "を交換し、"},
        {
            "text": f"{item_name} x{item_count}",
            "color": "aqua",
            "bold": True,
        },
        {"text": "を獲得しました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def _validate_resource_label_and_cost(item_name: str, cost_xp: int) -> None:
    if (
        not item_name.strip()
        or len(item_name) > 64
        or any(ord(character) < 32 for character in item_name)
    ):
        raise ValueError("resource item_name is invalid")
    if cost_xp <= 0:
        raise ValueError("cost_xp must be positive")


def minecraft_resource_shop_embed(
    packs: tuple[MinecraftResourcePack, ...],
) -> discord.Embed:
    embed = discord.Embed(
        title="Minecraft 資源交換所",
        description=(
            "活動で貯めたサーバーXPをMinecraft内の資源へ交換できます。\n"
            "手持ちのダイヤモンドとエメラルドも両替できます。\n"
            "連携したMinecraftアカウントでサーバーに参加中のみ交換できます。\n"
            "数量は小口から最大 **64個・1スタック** まで選べます。"
        ),
        color=discord.Color.teal(),
    )
    _add_bounded_fields(
        embed,
        "交換内容",
        (
            "**サーバーXP → 資源**\n"
            + _resource_pack_lines(packs)
            + "\n\n**手持ち資源 → 資源**\n"
            + "\n".join(
                f"`エメラルド x{emeralds}` → `ダイヤモンド x{diamonds}`"
                for emeralds, diamonds in EMERALD_DIAMOND_PACKS
            )
            + "\n"
            + "\n".join(
                f"`ダイヤモンド x{diamonds}` → `エメラルド x{emeralds}`"
                for diamonds, emeralds in DIAMOND_EMERALD_PACKS
            )
            + "\n\n**手持ち資源 → サーバーXP / ゲーム内**\n"
            "**エメラルド**\n"
            "`エメラルド x64` → `500 サーバーXP`\n\n"
            "**資材**\n"
            "`土 x64` → `30 サーバーXP` / `砂 x64` → `40 サーバーXP`\n"
            "`砂岩 x64` → `50 サーバーXP` / `深層岩 x64` → `35 サーバーXP`\n"
            "`深層岩の丸石 x64` → `35 サーバーXP` / "
            "`凝灰岩 x64` → `40 サーバーXP`\n"
            "1人1日 **3,000 サーバーXP** まで / 毎日0時・日本時間に更新\n"
            "本日の残り枠は処理時に確認し、上限超過時は資源を回収しません。\n"
            "名前や特殊データのない通常アイテムだけが対象です。"
        ),
    )
    _add_bounded_fields(
        embed,
        "🎮 ゲーム内コマンド",
        (
            "Java版・統合版: `/exchange` で交換メニューを開く\n"
            "XP→資源: `/exchange resource <資源ID> <個数>`\n"
            "両替: `/exchange emerald-diamond <32|64>`\n"
            "両替: `/exchange diamond-emerald <1|4>`\n"
            "資源売却: 対象アイテムを持って `/exchange buyback <1|2|4|8|16|max|all>`\n"
            "XP残高: `/exchange balance`\n" + _resource_command_help(packs)
        ),
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
            "XPから資源への交換とダイヤ・エメラルドの両替は、完了すると"
            "**Discordのログチャンネル**と**Minecraft内チャット**に通知されます。\n"
            "資源売却の結果・現在のサーバーXP・当日の残り売却枠は、"
            "Minecraft内で本人だけに表示されます。"
        ),
        inline=False,
    )
    embed.set_footer(text="残高・選択・確認画面は本人にのみ表示されます")
    return embed


def resource_catalog_management_embed(
    packs: tuple[MinecraftResourcePack, ...],
    *,
    revision: int,
    synchronized: bool,
    synchronization_error: str | None,
) -> discord.Embed:
    embed = discord.Embed(
        title="Minecraft 資源交換カタログ",
        description="現在の商品・受取個数・必要サーバーXPです。",
        color=discord.Color.green() if synchronized else discord.Color.orange(),
    )
    lines = [
        f"`{pack.item_id}` x{pack.item_count:,} → "
        f"{discord.utils.escape_markdown(pack.item_name)} / {pack.cost_xp:,} XP\n"
        for pack in packs
    ]
    _add_bounded_fields(embed, "商品一覧", "".join(lines))
    sync_text = (
        "確認済み" if synchronized else "未反映 / " + (synchronization_error or "理由不明")[:180]
    )
    embed.set_footer(text=f"世代 {revision} / Minecraft同期: {sync_text}")
    return embed


class MinecraftResourceShopPanelView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="XPで資源を購入",
        emoji="💎",
        style=discord.ButtonStyle.primary,
        custom_id="mc-resource-shop:open",
    )
    async def open_shop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_resource_shop_panel(interaction):
            await self.bot.show_minecraft_resource_shop(interaction)

    @discord.ui.button(
        label="ダイヤ・エメラルドを両替",
        emoji="🔄",
        style=discord.ButtonStyle.success,
        custom_id="mc-resource-shop:emerald-diamond",
    )
    async def emerald_diamond(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_resource_shop_panel(interaction):
            await self.bot.show_emerald_diamond_exchange(interaction)

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
            placeholder="交換する資源と数量を選択",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"{pack.item_name} x{pack.item_count:,}",
                    description=f"必要: {pack.cost_xp:,} サーバーXP",
                    emoji=_RESOURCE_EMOJIS.get(pack.item_id, "📦"),
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


def _resource_pack_lines(packs: tuple[MinecraftResourcePack, ...]) -> str:
    grouped: dict[str, list[MinecraftResourcePack]] = {}
    for pack in packs:
        grouped.setdefault(pack.item_id, []).append(pack)
    sections = []
    for item_id, item_packs in grouped.items():
        item_name = item_packs[0].item_name
        rates = "\n".join(
            f"`サーバーXP {pack.cost_xp:,}` → `{pack.item_name} x{pack.item_count:,}`"
            for pack in item_packs
        )
        sections.append(f"**{_RESOURCE_EMOJIS.get(item_id, '📦')} {item_name}**\n{rates}")
    return "\n\n".join(sections)


def _resource_command_help(packs: tuple[MinecraftResourcePack, ...]) -> str:
    grouped: dict[str, list[int]] = {}
    for pack in packs:
        grouped.setdefault(pack.item_id.removeprefix("minecraft:"), []).append(pack.item_count)
    return "利用可能な資源IDと個数 (Tab補完あり):\n" + "\n".join(
        f"{item_id}は `{'|'.join(str(count) for count in counts)}`"
        for item_id, counts in grouped.items()
    )


def _add_bounded_fields(embed: discord.Embed, name: str, value: str) -> None:
    chunks: list[str] = []
    current = ""
    for line in value.splitlines(keepends=True):
        while len(line) > 1024:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(line[:1024].rstrip())
            line = line[1024:]
        if current and len(current) + len(line) > 1024:
            chunks.append(current.rstrip())
            current = ""
        current += line
    if current:
        chunks.append(current.rstrip())
    for index, chunk in enumerate(chunks):
        embed.add_field(
            name=name if index == 0 else f"{name} (続き)",
            value=chunk,
            inline=False,
        )


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


class EmeraldDiamondPackSelect(discord.ui.Select):
    def __init__(self, bot: MinecraftDiscordBot, *, owner_id: int) -> None:
        self.bot = bot
        self.owner_id = owner_id
        super().__init__(
            placeholder="交換する数量を選択",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"エメラルド x{emeralds} → ダイヤモンド x{diamonds}",
                    value=str(emeralds),
                )
                for emeralds, diamonds in EMERALD_DIAMOND_PACKS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この交換メニューを使えるのは開いた本人だけです。", ephemeral=True
            )
            return
        try:
            emerald_count = int(self.values[0])
            diamond_count = dict(EMERALD_DIAMOND_PACKS)[emerald_count]
        except KeyError, ValueError:
            await interaction.response.send_message(
                "この交換内容は利用できません。", ephemeral=True
            )
            return
        embed = discord.Embed(
            title="交換内容の確認",
            description=(
                f"手持ちの **エメラルド x{emerald_count}** を使い、"
                f"**ダイヤモンド x{diamond_count}** を獲得します。\n"
                "サーバーXPは使用しません。"
            ),
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="交換前にご確認ください",
            value=(
                "Minecraftサーバーに参加した状態で交換してください。\n"
                "ダイヤモンドを受け取る空きがない場合、交換は行われません。"
            ),
            inline=False,
        )
        await interaction.response.send_message(
            embed=embed,
            view=EmeraldDiamondConfirmView(
                self.bot,
                owner_id=self.owner_id,
                request_id=str(uuid4()),
                emerald_count=emerald_count,
            ),
            ephemeral=True,
        )


class EmeraldDiamondPackSelectView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot, *, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.add_item(EmeraldDiamondPackSelect(bot, owner_id=owner_id))
        self.add_item(DiamondEmeraldPackSelect(bot, owner_id=owner_id))


class DiamondEmeraldPackSelect(discord.ui.Select):
    def __init__(self, bot: MinecraftDiscordBot, *, owner_id: int) -> None:
        self.bot = bot
        self.owner_id = owner_id
        super().__init__(
            placeholder="ダイヤモンド → エメラルド",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"ダイヤモンド x{diamonds} → エメラルド x{emeralds}",
                    value=str(diamonds),
                )
                for diamonds, emeralds in DIAMOND_EMERALD_PACKS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "この交換メニューを使えるのは開いた本人だけです。", ephemeral=True
            )
            return
        try:
            diamond_count = int(self.values[0])
            emerald_count = dict(DIAMOND_EMERALD_PACKS)[diamond_count]
        except KeyError, ValueError:
            await interaction.response.send_message(
                "この交換内容は利用できません。", ephemeral=True
            )
            return
        embed = discord.Embed(
            title="交換内容の確認",
            description=(
                f"手持ちの **ダイヤモンド x{diamond_count}** を使い、"
                f"**エメラルド x{emerald_count}** を獲得します。\n"
                "サーバーXPは使用しません。"
            ),
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="交換前にご確認ください",
            value=(
                "Minecraftサーバーに参加した状態で交換してください。\n"
                "エメラルドを受け取る空きがない場合、交換は行われません。"
            ),
            inline=False,
        )
        await interaction.response.send_message(
            embed=embed,
            view=DiamondEmeraldConfirmView(
                self.bot,
                owner_id=self.owner_id,
                request_id=str(uuid4()),
                diamond_count=diamond_count,
            ),
            ephemeral=True,
        )


class EmeraldDiamondConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        request_id: str,
        emerald_count: int,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id
        self.request_id = request_id
        self.emerald_count = emerald_count
        self._operation_lock = asyncio.Lock()
        self._completed = False

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
            result = await self.bot.confirm_emerald_diamond_exchange(
                interaction,
                request_id=self.request_id,
                emerald_count=self.emerald_count,
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
            if result.status == "completed":
                self._completed = True
                message = (
                    f"交換しました: エメラルド x{result.emerald_count} → "
                    f"ダイヤモンド x{result.diamond_count}"
                )
            else:
                self._enable_retry()
                await interaction.edit_original_response(view=self)
                message = {
                    "insufficient_emeralds": (
                        f"手持ちのエメラルドが{result.emerald_count}個未満のため"
                        "交換できませんでした。"
                    ),
                    "inventory_full": (
                        "ダイヤモンドを受け取る空きがないため交換できませんでした。"
                        "インベントリを空けて再試行してください。"
                    ),
                    "player_offline": (
                        "連携したMinecraftアカウントがオンラインではありません。"
                        "サーバーに参加してから再試行してください。"
                    ),
                    "account_ambiguous": (
                        "連携したMinecraftアカウントが複数同時にオンラインです。"
                        "交換に使う1アカウントだけで参加してから再試行してください。"
                    ),
                }[result.status]
            await interaction.followup.send(message, ephemeral=True)

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


class DiamondEmeraldConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        request_id: str,
        diamond_count: int,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id
        self.request_id = request_id
        self.diamond_count = diamond_count
        self._operation_lock = asyncio.Lock()
        self._completed = False

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
            result = await self.bot.confirm_diamond_emerald_exchange(
                interaction,
                request_id=self.request_id,
                diamond_count=self.diamond_count,
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
            if result.status == "completed":
                self._completed = True
                message = (
                    f"交換しました: ダイヤモンド x{result.diamond_count} → "
                    f"エメラルド x{result.emerald_count}"
                )
            else:
                self._enable_retry()
                await interaction.edit_original_response(view=self)
                message = {
                    "insufficient_diamonds": (
                        f"手持ちのダイヤモンドが{result.diamond_count}個未満のため"
                        "交換できませんでした。"
                    ),
                    "inventory_full": (
                        "エメラルドを受け取る空きがないため交換できませんでした。"
                        "インベントリを空けて再試行してください。"
                    ),
                    "player_offline": (
                        "連携したMinecraftアカウントがオンラインではありません。"
                        "サーバーに参加してから再試行してください。"
                    ),
                    "account_ambiguous": (
                        "連携したMinecraftアカウントが複数同時にオンラインです。"
                        "交換に使う1アカウントだけで参加してから再試行してください。"
                    ),
                }[result.status]
            await interaction.followup.send(message, ephemeral=True)

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
    "DIAMOND_EMERALD_PACKS",
    "EMERALD_DIAMOND_PACKS",
    "DiamondEmeraldConfirmView",
    "EmeraldDiamondConfirmView",
    "EmeraldDiamondPackSelectView",
    "MinecraftResourceConfirmView",
    "MinecraftResourcePackSelectView",
    "MinecraftResourceShopPanelView",
    "minecraft_resource_shop_embed",
    "resource_exchange_actionbar_command",
    "resource_exchange_tellraw_command",
    "resource_give_command",
    "wallet_text",
]
