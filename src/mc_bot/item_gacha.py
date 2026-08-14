from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot

_JST = ZoneInfo("Asia/Tokyo")
_SAFE_PLAYER_NAME = re.compile(r"\.?[A-Za-z0-9_]{1,32}")


@dataclass(frozen=True, slots=True)
class ItemGachaReward:
    key: str
    tier: str
    item_spec: str
    item_name: str
    item_count: int
    weight: int


# 800口で、N 35% / R 35% / SR 19% / SSR 7.5% / UR 3% / 幻 0.5%。
# Minecraft Java 26.2 の公式 recipe / loot_table を基準に入手経路を確認する。
# パネルにはランク確率だけを出し、景品内容と個別確率は抽選まで公開しない。
ITEM_GACHA_REWARDS = (
    ItemGachaReward("n_iron", "N", "minecraft:iron_ingot", "鉄インゴット", 24, 28),
    ItemGachaReward("n_gold", "N", "minecraft:gold_ingot", "金インゴット", 16, 28),
    ItemGachaReward("n_rocket", "N", "minecraft:firework_rocket", "ロケット花火", 48, 28),
    ItemGachaReward("n_xp_bottle", "N", "minecraft:experience_bottle", "エンチャントの瓶", 24, 28),
    ItemGachaReward("n_golden_carrot", "N", "minecraft:golden_carrot", "金のニンジン", 32, 28),
    ItemGachaReward("n_sea_lantern", "N", "minecraft:sea_lantern", "シーランタン", 16, 28),
    ItemGachaReward("n_slime", "N", "minecraft:slime_ball", "スライムボール", 16, 28),
    ItemGachaReward("n_quartz", "N", "minecraft:quartz", "ネザークォーツ", 48, 28),
    ItemGachaReward("n_redstone", "N", "minecraft:redstone", "レッドストーンダスト", 64, 28),
    ItemGachaReward("n_ender_pearl", "N", "minecraft:ender_pearl", "エンダーパール", 12, 28),
    ItemGachaReward("r_diamond", "R", "minecraft:diamond", "ダイヤモンド", 3, 35),
    ItemGachaReward("r_golden_apple", "R", "minecraft:golden_apple", "金のリンゴ", 3, 35),
    ItemGachaReward("r_shulker_shell", "R", "minecraft:shulker_shell", "シュルカーの殻", 2, 35),
    ItemGachaReward("r_breeze_rod", "R", "minecraft:breeze_rod", "ブリーズロッド", 8, 35),
    ItemGachaReward(
        "r_wither_skull",
        "R",
        "minecraft:wither_skeleton_skull",
        "ウィザースケルトンの頭蓋骨",
        2,
        35,
    ),
    ItemGachaReward(
        "r_ominous_trial_key",
        "R",
        "minecraft:ominous_trial_key",
        "不吉な試練の鍵",
        1,
        35,
    ),
    ItemGachaReward(
        "r_mending",
        "R",
        "minecraft:enchanted_book[stored_enchantments={mending:1}]",
        "修繕のエンチャント本",
        1,
        35,
    ),
    ItemGachaReward(
        "r_silk_touch",
        "R",
        "minecraft:enchanted_book[stored_enchantments={silk_touch:1}]",
        "シルクタッチのエンチャント本",
        1,
        35,
    ),
    ItemGachaReward(
        "sr_diamond_block", "SR", "minecraft:diamond_block", "ダイヤモンドブロック", 1, 31
    ),
    ItemGachaReward("sr_ancient_debris", "SR", "minecraft:ancient_debris", "古代の残骸", 3, 31),
    ItemGachaReward("sr_totem", "SR", "minecraft:totem_of_undying", "不死のトーテム", 1, 30),
    ItemGachaReward("sr_heavy_core", "SR", "minecraft:heavy_core", "ヘビーコア", 1, 30),
    ItemGachaReward("sr_trident", "SR", "minecraft:trident", "トライデント", 1, 30),
    ItemGachaReward(
        "ssr_netherite", "SSR", "minecraft:netherite_ingot", "ネザライトインゴット", 1, 12
    ),
    ItemGachaReward("ssr_elytra", "SSR", "minecraft:elytra", "エリトラ", 1, 12),
    ItemGachaReward("ssr_beacon", "SSR", "minecraft:beacon", "ビーコン", 1, 12),
    ItemGachaReward(
        "ssr_enchanted_apple",
        "SSR",
        "minecraft:enchanted_golden_apple",
        "エンチャントされた金のリンゴ",
        2,
        12,
    ),
    ItemGachaReward(
        "ssr_diamond_blocks",
        "SSR",
        "minecraft:diamond_block",
        "ダイヤモンドブロック",
        3,
        12,
    ),
    ItemGachaReward(
        "ur_netherite", "UR", "minecraft:netherite_ingot", "ネザライトインゴット", 3, 6
    ),
    ItemGachaReward(
        "ur_enchanted_apple",
        "UR",
        "minecraft:enchanted_golden_apple",
        "エンチャントされた金のリンゴ",
        4,
        6,
    ),
    ItemGachaReward("ur_beacon", "UR", "minecraft:beacon", "ビーコン", 2, 6),
    ItemGachaReward(
        "ur_diamond_blocks",
        "UR",
        "minecraft:diamond_block",
        "ダイヤモンドブロック",
        6,
        6,
    ),
    ItemGachaReward(
        "mythic_sword",
        "MYTHIC",
        "minecraft:netherite_sword[enchantments={sharpness:5,sweeping_edge:3,looting:3,fire_aspect:2,knockback:2,unbreaking:3,mending:1}]",
        "最大強化ネザライトの剣",
        1,
        1,
    ),
    ItemGachaReward(
        "mythic_pickaxe",
        "MYTHIC",
        "minecraft:netherite_pickaxe[enchantments={efficiency:5,fortune:3,unbreaking:3,mending:1}]",
        "最大強化ネザライトのツルハシ",
        1,
        1,
    ),
    ItemGachaReward(
        "mythic_chestplate",
        "MYTHIC",
        "minecraft:netherite_chestplate[enchantments={protection:4,thorns:3,unbreaking:3,mending:1}]",
        "最大強化ネザライトのチェストプレート",
        1,
        1,
    ),
    ItemGachaReward(
        "mythic_elytra",
        "MYTHIC",
        "minecraft:elytra[enchantments={unbreaking:3,mending:1}]",
        "修繕付きエリトラ",
        1,
        1,
    ),
)

_REWARDS_BY_KEY = {reward.key: reward for reward in ITEM_GACHA_REWARDS}
_TOTAL_WEIGHT = sum(reward.weight for reward in ITEM_GACHA_REWARDS)
if _TOTAL_WEIGHT != 800 or len(_REWARDS_BY_KEY) != len(ITEM_GACHA_REWARDS):
    raise RuntimeError("item gacha reward table is invalid")


def item_gacha_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(_JST).date().isoformat()


def draw_item_gacha_reward(roll: int | None = None) -> ItemGachaReward:
    selected = secrets.randbelow(_TOTAL_WEIGHT) if roll is None else roll
    if not 0 <= selected < _TOTAL_WEIGHT:
        raise ValueError("roll is outside the item gacha table")
    cursor = 0
    for reward in ITEM_GACHA_REWARDS:
        cursor += reward.weight
        if selected < cursor:
            return reward
    raise RuntimeError("item gacha reward table did not select a reward")


def get_item_gacha_reward(reward_key: str) -> ItemGachaReward:
    try:
        return _REWARDS_BY_KEY[reward_key]
    except KeyError as error:
        raise ValueError("unknown item gacha reward") from error


def item_gacha_give_command(player_name: str, reward_key: str) -> str:
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
    reward = get_item_gacha_reward(reward_key)
    return f"give {player_name} {reward.item_spec} {reward.item_count}"


def item_gacha_tellraw_command(player_name: str, reward_key: str) -> str:
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
    reward = get_item_gacha_reward(reward_key)
    tier = item_gacha_tier_label(reward.tier)
    color = {
        "N": "gray",
        "R": "green",
        "SR": "aqua",
        "SSR": "light_purple",
        "UR": "gold",
        "MYTHIC": "red",
    }[reward.tier]
    components = [
        {"text": "🎁 "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんがアイテムガチャで "},
        {"text": f"【{tier}】", "color": color, "bold": True},
        {
            "text": f"{reward.item_name} x{reward.item_count}",
            "color": color,
            "bold": True,
        },
        {"text": " を獲得しました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def item_gacha_result_embed(player_name: str, reward_key: str) -> discord.Embed:
    reward = get_item_gacha_reward(reward_key)
    tier = item_gacha_tier_label(reward.tier)
    color = {
        "N": discord.Color.light_grey(),
        "R": discord.Color.green(),
        "SR": discord.Color.teal(),
        "SSR": discord.Color.purple(),
        "UR": discord.Color.gold(),
        "MYTHIC": discord.Color.red(),
    }[reward.tier]
    return discord.Embed(
        title=f"🎁 アイテムガチャ【{tier}】",
        description=(
            f"**{discord.utils.escape_markdown(player_name)}さん**が\n"
            f"**{reward.item_name} x{reward.item_count}** を獲得しました!"
        ),
        color=color,
    )


def item_gacha_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎁 Minecraft アイテムガチャ",
        description=(
            "連携したMinecraftアカウントで参加中に、**1日1回**引けます。\n"
            "何が出るかは受け取るまで秘密。景品はその場でMinecraftへ届きます。"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="ランク確率",
        value="`N 35%` `R 35%` `SR 19%`\n`SSR 7.5%` `UR 3%` `幻 0.5%`",
        inline=False,
    )
    embed.add_field(
        name="更新と通知",
        value=(
            "毎日 **日本時間0:00** に更新します。\n"
            "Nを含むすべての結果をMinecraft内チャットとDiscordログへ通知します。\n"
            "インベントリに入らない分は足元へドロップします。"
        ),
        inline=False,
    )
    embed.set_footer(text="景品の内容と個別確率は、引いてからのお楽しみです")
    return embed


def item_gacha_tier_label(tier: str) -> str:
    return "幻" if tier == "MYTHIC" else tier


class MinecraftItemGachaPanelView(discord.ui.View):
    def __init__(self, bot: MinecraftDiscordBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="今日のガチャを引く",
        emoji="🎁",
        style=discord.ButtonStyle.primary,
        custom_id="mc-item-gacha:draw",
    )
    async def draw(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_item_gacha_panel(interaction):
            await self.bot.draw_minecraft_item_gacha(interaction)


__all__ = [
    "ITEM_GACHA_REWARDS",
    "ItemGachaReward",
    "MinecraftItemGachaPanelView",
    "draw_item_gacha_reward",
    "get_item_gacha_reward",
    "item_gacha_day",
    "item_gacha_give_command",
    "item_gacha_panel_embed",
    "item_gacha_result_embed",
    "item_gacha_tellraw_command",
    "item_gacha_tier_label",
]
