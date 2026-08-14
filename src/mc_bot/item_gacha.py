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


def _enchanted_book(
    key: str,
    tier: str,
    enchantment: str,
    level: int,
    item_name: str,
    weight: int,
) -> ItemGachaReward:
    return ItemGachaReward(
        key,
        tier,
        f"minecraft:enchanted_book[stored_enchantments={{{enchantment}:{level}}}]",
        item_name,
        1,
        weight,
    )


# 800口で、N 35% / R 35% / SR 19% / SSR 7.5% / UR 3% / 幻 0.5%。
# Minecraft Java 26.2 の公式 recipe / loot_table / enchantment を基準に確認する。
# パネルにはランク確率だけを出し、景品内容と個別確率は抽選まで公開しない。
ITEM_GACHA_REWARDS = (
    ItemGachaReward("n_iron", "N", "minecraft:iron_ingot", "鉄インゴット", 24, 12),
    ItemGachaReward("n_gold", "N", "minecraft:gold_ingot", "金インゴット", 16, 12),
    ItemGachaReward("n_rocket", "N", "minecraft:firework_rocket", "ロケット花火", 48, 12),
    ItemGachaReward("n_xp_bottle", "N", "minecraft:experience_bottle", "エンチャントの瓶", 24, 12),
    ItemGachaReward("n_golden_carrot", "N", "minecraft:golden_carrot", "金のニンジン", 32, 12),
    ItemGachaReward("n_sea_lantern", "N", "minecraft:sea_lantern", "シーランタン", 16, 12),
    ItemGachaReward("n_slime", "N", "minecraft:slime_ball", "スライムボール", 16, 12),
    ItemGachaReward("n_quartz", "N", "minecraft:quartz", "ネザークォーツ", 48, 12),
    ItemGachaReward("n_redstone", "N", "minecraft:redstone", "レッドストーンダスト", 64, 12),
    ItemGachaReward("n_ender_pearl", "N", "minecraft:ender_pearl", "エンダーパール", 12, 12),
    _enchanted_book("n_bane", "N", "bane_of_arthropods", 5, "虫特効Vのエンチャント本", 10),
    _enchanted_book(
        "n_blast_protection", "N", "blast_protection", 4, "爆発耐性IVのエンチャント本", 11
    ),
    _enchanted_book("n_channeling", "N", "channeling", 1, "召雷のエンチャント本", 11),
    _enchanted_book(
        "n_fire_protection", "N", "fire_protection", 4, "火炎耐性IVのエンチャント本", 11
    ),
    _enchanted_book("n_flame", "N", "flame", 1, "フレイムのエンチャント本", 11),
    _enchanted_book("n_frost_walker", "N", "frost_walker", 2, "氷渡りIIのエンチャント本", 11),
    _enchanted_book("n_impaling", "N", "impaling", 5, "水生特効Vのエンチャント本", 10),
    _enchanted_book(
        "n_knockback",
        "N",
        "knockback",
        2,
        "ノックバックIIのエンチャント本",  # noqa: RUF001 - Minecraft公式の日本語名
        10,
    ),
    _enchanted_book("n_loyalty", "N", "loyalty", 3, "忠誠IIIのエンチャント本", 11),
    _enchanted_book("n_multishot", "N", "multishot", 1, "拡散のエンチャント本", 11),
    _enchanted_book("n_piercing", "N", "piercing", 4, "貫通IVのエンチャント本", 10),
    _enchanted_book(
        "n_projectile_protection",
        "N",
        "projectile_protection",
        4,
        "飛び道具耐性IVのエンチャント本",
        11,
    ),
    _enchanted_book("n_punch", "N", "punch", 2, "パンチIIのエンチャント本", 10),
    _enchanted_book("n_quick_charge", "N", "quick_charge", 3, "高速装填IIIのエンチャント本", 11),
    _enchanted_book("n_thorns", "N", "thorns", 3, "棘の鎧IIIのエンチャント本", 11),
    ItemGachaReward("r_diamond", "R", "minecraft:diamond", "ダイヤモンド", 3, 10),
    ItemGachaReward("r_golden_apple", "R", "minecraft:golden_apple", "金のリンゴ", 3, 10),
    ItemGachaReward("r_shulker_shell", "R", "minecraft:shulker_shell", "シュルカーの殻", 2, 10),
    ItemGachaReward("r_breeze_rod", "R", "minecraft:breeze_rod", "ブリーズロッド", 8, 10),
    ItemGachaReward(
        "r_wither_skull",
        "R",
        "minecraft:wither_skeleton_skull",
        "ウィザースケルトンの頭蓋骨",
        2,
        10,
    ),
    ItemGachaReward(
        "r_ominous_trial_key",
        "R",
        "minecraft:ominous_trial_key",
        "不吉な試練の鍵",
        1,
        10,
    ),
    _enchanted_book("r_mending", "R", "mending", 1, "修繕のエンチャント本", 11),
    _enchanted_book("r_fortune", "R", "fortune", 3, "幸運IIIのエンチャント本", 11),
    _enchanted_book("r_efficiency", "R", "efficiency", 5, "効率強化Vのエンチャント本", 11),
    _enchanted_book("r_unbreaking", "R", "unbreaking", 3, "耐久力IIIのエンチャント本", 11),
    _enchanted_book("r_silk_touch", "R", "silk_touch", 1, "シルクタッチのエンチャント本", 11),
    _enchanted_book("r_protection", "R", "protection", 4, "ダメージ軽減IVのエンチャント本", 11),
    _enchanted_book(
        "r_feather_falling", "R", "feather_falling", 4, "落下耐性IVのエンチャント本", 11
    ),
    _enchanted_book("r_looting", "R", "looting", 3, "ドロップ増加IIIのエンチャント本", 11),
    _enchanted_book("r_sharpness", "R", "sharpness", 5, "ダメージ増加Vのエンチャント本", 11),
    _enchanted_book("r_power", "R", "power", 5, "射撃ダメージ増加Vのエンチャント本", 11),
    _enchanted_book("r_infinity", "R", "infinity", 1, "無限のエンチャント本", 11),
    _enchanted_book("r_depth_strider", "R", "depth_strider", 3, "水中歩行IIIのエンチャント本", 11),
    _enchanted_book("r_respiration", "R", "respiration", 3, "水中呼吸IIIのエンチャント本", 11),
    _enchanted_book("r_aqua_affinity", "R", "aqua_affinity", 1, "水中採掘のエンチャント本", 11),
    _enchanted_book(
        "r_luck_of_the_sea", "R", "luck_of_the_sea", 3, "宝釣りIIIのエンチャント本", 11
    ),
    _enchanted_book("r_lure", "R", "lure", 3, "入れ食いIIIのエンチャント本", 11),
    _enchanted_book("r_fire_aspect", "R", "fire_aspect", 2, "火属性IIのエンチャント本", 11),
    _enchanted_book("r_smite", "R", "smite", 5, "アンデッド特効Vのエンチャント本", 11),
    _enchanted_book(
        "r_sweeping_edge", "R", "sweeping_edge", 3, "範囲ダメージ増加IIIのエンチャント本", 11
    ),
    _enchanted_book("r_riptide", "R", "riptide", 3, "激流IIIのエンチャント本", 11),
    ItemGachaReward(
        "sr_diamond_block", "SR", "minecraft:diamond_block", "ダイヤモンドブロック", 1, 16
    ),
    ItemGachaReward("sr_ancient_debris", "SR", "minecraft:ancient_debris", "古代の残骸", 3, 16),
    ItemGachaReward("sr_totem", "SR", "minecraft:totem_of_undying", "不死のトーテム", 1, 15),
    ItemGachaReward("sr_heavy_core", "SR", "minecraft:heavy_core", "ヘビーコア", 1, 15),
    ItemGachaReward("sr_trident", "SR", "minecraft:trident", "トライデント", 1, 15),
    _enchanted_book(
        "sr_swift_sneak", "SR", "swift_sneak", 3, "スニーク速度上昇IIIのエンチャント本", 15
    ),
    _enchanted_book(
        "sr_soul_speed", "SR", "soul_speed", 3, "ソウルスピードIIIのエンチャント本", 15
    ),
    _enchanted_book("sr_density", "SR", "density", 5, "重撃Vのエンチャント本", 15),
    _enchanted_book("sr_breach", "SR", "breach", 4, "防具貫通IVのエンチャント本", 15),
    _enchanted_book("sr_lunge", "SR", "lunge", 3, "突進IIIのエンチャント本", 15),
    ItemGachaReward(
        "ssr_netherite", "SSR", "minecraft:netherite_ingot", "ネザライトインゴット", 1, 10
    ),
    ItemGachaReward("ssr_elytra", "SSR", "minecraft:elytra", "エリトラ", 1, 10),
    ItemGachaReward("ssr_beacon", "SSR", "minecraft:beacon", "ビーコン", 1, 10),
    ItemGachaReward(
        "ssr_enchanted_apple",
        "SSR",
        "minecraft:enchanted_golden_apple",
        "エンチャントされた金のリンゴ",
        2,
        10,
    ),
    ItemGachaReward(
        "ssr_diamond_blocks",
        "SSR",
        "minecraft:diamond_block",
        "ダイヤモンドブロック",
        3,
        10,
    ),
    _enchanted_book(
        "ssr_wind_burst", "SSR", "wind_burst", 3, "ウィンドバーストIIIのエンチャント本", 10
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


def item_gacha_result_embed(
    *,
    player_name: str,
    discord_user_id: int,
    reward_key: str,
) -> discord.Embed:
    if discord_user_id <= 0:
        raise ValueError("discord_user_id must be positive")
    reward = get_item_gacha_reward(reward_key)
    player_name = discord.utils.escape_markdown(player_name)
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
            f"**{player_name} (<@{discord_user_id}>) さん** が\n"
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
