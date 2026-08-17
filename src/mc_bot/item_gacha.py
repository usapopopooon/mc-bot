from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

import discord

from mc_bot.player_names import is_safe_server_player_name

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot

_JST = ZoneInfo("Asia/Tokyo")
ITEM_GACHA_NORMAL_COST_XP = 100
ITEM_GACHA_PREMIUM_COST_XP = 1_000
ITEM_GACHA_DAILY_LIMIT = 3
ITEM_GACHA_COST_XP = ITEM_GACHA_NORMAL_COST_XP
type ItemGachaKind = Literal["normal", "premium"]

_DRAW_TABLE_SIZE = 400
_TIER_ORDER = ("N", "R", "SR", "SSR", "UR", "MYTHIC")
_TIER_WEIGHTS_BY_KIND: dict[ItemGachaKind, dict[str, int]] = {
    "normal": {
        "N": 220,
        "R": 112,
        "SR": 44,
        "SSR": 16,
        "UR": 7,
        "MYTHIC": 1,
    },
    "premium": {
        "N": 0,
        "R": 280,
        "SR": 80,
        "SSR": 28,
        "UR": 10,
        "MYTHIC": 2,
    },
}


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


# レアリティを先に公開確率で抽選し、そのランク内では既存weightを相対比として使う。
# 通常は N 55% / R 28% / SR 11% / SSR 4% / UR 1.75% / 幻 0.25%、
# R以上確定は R 70% / SR 20% / SSR 7% / UR 2.5% / 幻 0.5%。
# Minecraft Java 26.2 の公式 recipe / loot_table / enchantment を基準に確認する。
# パネルにはランク確率だけを出し、景品内容と個別確率は抽選まで公開しない。
# keyは予約済み抽選の復旧に使うため、レアリティ変更後も既存値を維持する。
ITEM_GACHA_REWARDS = (
    ItemGachaReward("n_iron", "N", "minecraft:iron_ingot", "鉄インゴット", 24, 13),
    ItemGachaReward("n_gold", "N", "minecraft:gold_ingot", "金インゴット", 16, 13),
    ItemGachaReward("n_rocket_bulk", "N", "minecraft:firework_rocket", "ロケット花火", 64, 13),
    ItemGachaReward(
        "n_xp_bottle_bulk", "N", "minecraft:experience_bottle", "エンチャントの瓶", 64, 13
    ),
    ItemGachaReward("n_golden_carrot_bulk", "N", "minecraft:golden_carrot", "金のニンジン", 64, 13),
    ItemGachaReward("n_sea_lantern", "N", "minecraft:sea_lantern", "シーランタン", 16, 13),
    ItemGachaReward("n_slime", "N", "minecraft:slime_ball", "スライムボール", 16, 13),
    ItemGachaReward("n_quartz", "N", "minecraft:quartz", "ネザークォーツ", 48, 13),
    ItemGachaReward("n_redstone", "N", "minecraft:redstone", "レッドストーンダスト", 64, 12),
    ItemGachaReward("n_ender_pearl_bulk", "N", "minecraft:ender_pearl", "エンダーパール", 16, 12),
    ItemGachaReward("n_sulfur", "N", "minecraft:sulfur", "硫黄", 32, 12),
    ItemGachaReward("n_cinnabar", "N", "minecraft:cinnabar", "辰砂", 32, 12),
    ItemGachaReward("n_amethyst", "N", "minecraft:amethyst_shard", "アメジストの欠片", 32, 12),
    ItemGachaReward("n_glowstone", "N", "minecraft:glowstone", "グロウストーン", 24, 12),
    ItemGachaReward(
        "n_prismarine_crystals",
        "N",
        "minecraft:prismarine_crystals",
        "プリズマリンクリスタル",
        24,
        12,
    ),
    ItemGachaReward(
        "n_phantom_membrane", "N", "minecraft:phantom_membrane", "ファントムの皮膜", 8, 12
    ),
    ItemGachaReward("n_honeycomb", "N", "minecraft:honeycomb", "ハニカム", 32, 12),
    ItemGachaReward("n_wind_charge_bulk", "N", "minecraft:wind_charge", "ウィンドチャージ", 64, 12),
    ItemGachaReward(
        "n_chorus_fruit_bulk", "N", "minecraft:chorus_fruit", "コーラスフルーツ", 64, 12
    ),
    ItemGachaReward("r_diamond_spear", "N", "minecraft:diamond_spear", "ダイヤモンドの槍", 1, 12),
    ItemGachaReward(
        "r_sulfur_cube_bucket",
        "N",
        "minecraft:sulfur_cube_bucket",
        "サルファーキューブ入りバケツ",
        1,
        12,
    ),
    ItemGachaReward(
        "n_poisonous_potato",
        "N",
        "minecraft:poisonous_potato",
        "産地直送・毒入りジャガイモ",
        64,
        12,
    ),
    ItemGachaReward("n_dead_bush", "N", "minecraft:dead_bush", "枯れ木の栽培セット", 16, 12),
    ItemGachaReward("n_legendary_dirt", "N", "minecraft:dirt", "由緒正しい土", 64, 12),
    ItemGachaReward("n_steak_bulk", "N", "minecraft:cooked_beef", "ステーキ", 64, 12),
    ItemGachaReward("n_cooked_porkchop_bulk", "N", "minecraft:cooked_porkchop", "焼き豚", 64, 12),
    ItemGachaReward("n_pumpkin_pie_bulk", "N", "minecraft:pumpkin_pie", "パンプキンパイ", 64, 12),
    ItemGachaReward(
        "n_honey_bottle_bulk", "N", "minecraft:honey_bottle", "ハチミツ入りの瓶", 24, 12
    ),
    ItemGachaReward("n_cookie", "N", "minecraft:cookie", "クッキー", 64, 12),
    ItemGachaReward(
        "n_night_vision_potion_bulk",
        "N",
        'minecraft:potion[potion_contents="minecraft:long_night_vision"]',
        "暗視のポーション 8:00",
        6,
        12,
    ),
    ItemGachaReward(
        "n_water_breathing_potion_bulk",
        "N",
        'minecraft:potion[potion_contents="minecraft:long_water_breathing"]',
        "水中呼吸のポーション 8:00",
        6,
        12,
    ),
    _enchanted_book("n_bane", "N", "bane_of_arthropods", 5, "虫特効Vのエンチャント本", 12),
    _enchanted_book(
        "n_blast_protection", "N", "blast_protection", 4, "爆発耐性IVのエンチャント本", 12
    ),
    _enchanted_book("n_channeling", "N", "channeling", 1, "召雷のエンチャント本", 12),
    _enchanted_book(
        "n_fire_protection", "N", "fire_protection", 4, "火炎耐性IVのエンチャント本", 12
    ),
    _enchanted_book("n_flame", "N", "flame", 1, "フレイムのエンチャント本", 12),
    _enchanted_book("n_frost_walker", "N", "frost_walker", 2, "氷渡りIIのエンチャント本", 12),
    _enchanted_book("n_impaling", "N", "impaling", 5, "水生特効Vのエンチャント本", 12),
    _enchanted_book(
        "n_knockback",
        "N",
        "knockback",
        2,
        "ノックバックIIのエンチャント本",  # noqa: RUF001 - Minecraft公式の日本語名
        12,
    ),
    _enchanted_book("n_loyalty", "N", "loyalty", 3, "忠誠IIIのエンチャント本", 12),
    _enchanted_book("n_multishot", "N", "multishot", 1, "拡散のエンチャント本", 12),
    _enchanted_book("n_piercing", "N", "piercing", 4, "貫通IVのエンチャント本", 12),
    _enchanted_book(
        "n_projectile_protection",
        "N",
        "projectile_protection",
        4,
        "飛び道具耐性IVのエンチャント本",
        12,
    ),
    _enchanted_book("n_punch", "N", "punch", 2, "パンチIIのエンチャント本", 12),
    _enchanted_book("n_quick_charge", "N", "quick_charge", 3, "高速装填IIIのエンチャント本", 12),
    _enchanted_book("n_thorns", "N", "thorns", 3, "棘の鎧IIIのエンチャント本", 12),
    ItemGachaReward("r_diamond", "R", "minecraft:diamond", "ダイヤモンド", 3, 12),
    ItemGachaReward("r_golden_apple_bulk", "R", "minecraft:golden_apple", "金のリンゴ", 6, 12),
    ItemGachaReward("r_shulker_box", "R", "minecraft:shulker_box", "シュルカーボックス", 2, 12),
    ItemGachaReward("r_breeze_rod_bulk", "R", "minecraft:breeze_rod", "ブリーズロッド", 12, 12),
    ItemGachaReward(
        "r_rocket_crate",
        "R",
        "minecraft:firework_rocket",
        "ロケット花火",
        128,
        12,
    ),
    ItemGachaReward("r_iron_block_crate", "R", "minecraft:iron_block", "鉄ブロック", 16, 12),
    ItemGachaReward("r_ender_pearl_crate", "R", "minecraft:ender_pearl", "エンダーパール", 32, 12),
    ItemGachaReward("r_obsidian_crate", "R", "minecraft:obsidian", "黒曜石", 64, 12),
    ItemGachaReward(
        "r_recovery_compass",
        "R",
        "minecraft:recovery_compass",
        "リカバリーコンパス",
        1,
        12,
    ),
    ItemGachaReward("r_sniffer_egg", "R", "minecraft:sniffer_egg", "スニッファーの卵", 1, 12),
    ItemGachaReward(
        "r_xp_bottle_crate",
        "R",
        "minecraft:experience_bottle",
        "エンチャントの瓶",
        64,
        12,
    ),
    ItemGachaReward(
        "r_netherite_upgrade",
        "R",
        "minecraft:netherite_upgrade_smithing_template",
        "鍛冶型: ネザライト強化",
        1,
        12,
    ),
    ItemGachaReward(
        "r_flow_trim",
        "R",
        "minecraft:flow_armor_trim_smithing_template",
        "鍛冶型: 旋風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_bolt_trim",
        "R",
        "minecraft:bolt_armor_trim_smithing_template",
        "鍛冶型: ネジ止め風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_tide_trim",
        "R",
        "minecraft:tide_armor_trim_smithing_template",
        "鍛冶型: 潮流風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_sentry_trim",
        "R",
        "minecraft:sentry_armor_trim_smithing_template",
        "鍛冶型: 略奪者風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_dune_trim",
        "R",
        "minecraft:dune_armor_trim_smithing_template",
        "鍛冶型: 砂丘風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_coast_trim",
        "R",
        "minecraft:coast_armor_trim_smithing_template",
        "鍛冶型: 海洋風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_wild_trim",
        "R",
        "minecraft:wild_armor_trim_smithing_template",
        "鍛冶型: 大自然風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_snout_trim",
        "R",
        "minecraft:snout_armor_trim_smithing_template",
        "鍛冶型: ブタの鼻風の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_rib_trim",
        "R",
        "minecraft:rib_armor_trim_smithing_template",
        "鍛冶型: あばら模様の装飾",
        1,
        12,
    ),
    ItemGachaReward(
        "r_wayfinder_trim",
        "R",
        "minecraft:wayfinder_armor_trim_smithing_template",
        "鍛冶型: 先駆者風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "r_shaper_trim",
        "R",
        "minecraft:shaper_armor_trim_smithing_template",
        "鍛冶型: 職人風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "r_raiser_trim",
        "R",
        "minecraft:raiser_armor_trim_smithing_template",
        "鍛冶型: 牧者風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "r_host_trim",
        "R",
        "minecraft:host_armor_trim_smithing_template",
        "鍛冶型: 主人風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "r_music_relic",
        "R",
        "minecraft:music_disc_relic",
        "レコード: Aaron Cherof - Relic",
        1,
        11,
    ),
    ItemGachaReward(
        "r_music_5",
        "R",
        "minecraft:music_disc_5",
        "レコード: Samuel Åberg - 5",
        1,
        11,
    ),
    ItemGachaReward(
        "r_music_precipice",
        "R",
        "minecraft:music_disc_precipice",
        "レコード: Aaron Cherof - Precipice",
        1,
        11,
    ),
    ItemGachaReward(
        "sr_music_creator_box",
        "R",
        "minecraft:music_disc_creator_music_box",
        "レコード: Lena Raine - Creator (オルゴール)",
        1,
        11,
    ),
    ItemGachaReward(
        "r_fire_resistance_potion_bulk",
        "R",
        'minecraft:potion[potion_contents="minecraft:long_fire_resistance"]',
        "耐火のポーション 8:00",
        6,
        11,
    ),
    ItemGachaReward(
        "r_slow_falling_potion_bulk",
        "R",
        'minecraft:potion[potion_contents="minecraft:long_slow_falling"]',
        "低速落下のポーション 4:00",
        6,
        11,
    ),
    ItemGachaReward(
        "r_healing_splash_potion_bulk",
        "R",
        'minecraft:splash_potion[potion_contents="minecraft:strong_healing"]',
        "治癒のスプラッシュポーション II",
        8,
        11,
    ),
    ItemGachaReward(
        "r_strength_potion_bulk",
        "R",
        'minecraft:potion[potion_contents="minecraft:strong_strength"]',
        "力のポーション II",
        6,
        11,
    ),
    ItemGachaReward(
        "r_regeneration_potion_bulk",
        "R",
        'minecraft:potion[potion_contents="minecraft:strong_regeneration"]',
        "再生のポーション II",
        6,
        11,
    ),
    ItemGachaReward(
        "r_swiftness_potion",
        "R",
        'minecraft:potion[potion_contents="minecraft:strong_swiftness"]',
        "俊敏のポーション II 1:30",
        6,
        11,
    ),
    _enchanted_book("r_silk_touch", "R", "silk_touch", 1, "シルクタッチのエンチャント本", 11),
    _enchanted_book(
        "r_feather_falling", "R", "feather_falling", 4, "落下耐性IVのエンチャント本", 11
    ),
    _enchanted_book("r_looting", "R", "looting", 3, "ドロップ増加IIIのエンチャント本", 11),
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
        "sr_diamond_block", "SR", "minecraft:diamond_block", "ダイヤモンドブロック", 1, 11
    ),
    ItemGachaReward("sr_ancient_debris", "SR", "minecraft:ancient_debris", "古代の残骸", 3, 11),
    ItemGachaReward("sr_totem", "SR", "minecraft:totem_of_undying", "不死のトーテム", 1, 11),
    ItemGachaReward(
        "r_wither_skull",
        "SR",
        "minecraft:wither_skeleton_skull",
        "ウィザースケルトンの頭蓋骨",
        2,
        10,
    ),
    ItemGachaReward("sr_trident", "SR", "minecraft:trident", "トライデント", 1, 11),
    ItemGachaReward(
        "sr_diamond_nautilus_armor",
        "SR",
        "minecraft:diamond_nautilus_armor",
        "ダイヤモンドのオウムガイの鎧",
        1,
        10,
    ),
    ItemGachaReward("sr_conduit", "SR", "minecraft:conduit", "コンジット", 1, 11),
    ItemGachaReward(
        "sr_ward_trim",
        "SR",
        "minecraft:ward_armor_trim_smithing_template",
        "鍛冶型: 監獄風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "sr_spire_trim",
        "SR",
        "minecraft:spire_armor_trim_smithing_template",
        "鍛冶型: 尖塔風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "sr_eye_trim",
        "SR",
        "minecraft:eye_armor_trim_smithing_template",
        "鍛冶型: エンダーアイ風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "sr_vex_trim",
        "SR",
        "minecraft:vex_armor_trim_smithing_template",
        "鍛冶型: ヴェックス風の装飾",
        1,
        11,
    ),
    ItemGachaReward(
        "sr_music_creator",
        "SR",
        "minecraft:music_disc_creator",
        "レコード: Lena Raine - Creator",
        1,
        11,
    ),
    ItemGachaReward("sr_creeper_head", "SR", "minecraft:creeper_head", "クリーパーの頭", 1, 11),
    ItemGachaReward(
        "sr_harming_lingering_potion_bulk",
        "SR",
        'minecraft:lingering_potion[potion_contents="minecraft:strong_harming"]',
        "負傷の残留ポーション II",
        8,
        11,
    ),
    ItemGachaReward(
        "sr_turtle_master_splash_potion_bulk",
        "SR",
        'minecraft:splash_potion[potion_contents="minecraft:strong_turtle_master"]',
        "タートルマスターのスプラッシュポーション II",
        6,
        11,
    ),
    ItemGachaReward(
        "sr_wind_charged_lingering_potion_bulk",
        "SR",
        'minecraft:lingering_potion[potion_contents="minecraft:wind_charged"]',
        "蓄風の残留ポーション",
        6,
        11,
    ),
    ItemGachaReward(
        "sr_weaving_lingering_potion_bulk",
        "SR",
        'minecraft:lingering_potion[potion_contents="minecraft:weaving"]',
        "巣張りの残留ポーション",
        6,
        11,
    ),
    _enchanted_book("r_mending", "SR", "mending", 1, "修繕のエンチャント本", 10),
    _enchanted_book("r_fortune", "SR", "fortune", 3, "幸運IIIのエンチャント本", 11),
    _enchanted_book("r_efficiency", "SR", "efficiency", 5, "効率強化Vのエンチャント本", 10),
    _enchanted_book("r_unbreaking", "SR", "unbreaking", 3, "耐久力IIIのエンチャント本", 11),
    _enchanted_book("r_protection", "SR", "protection", 4, "ダメージ軽減IVのエンチャント本", 11),
    _enchanted_book("r_sharpness", "SR", "sharpness", 5, "ダメージ増加Vのエンチャント本", 11),
    _enchanted_book(
        "sr_swift_sneak", "SR", "swift_sneak", 3, "スニーク速度上昇IIIのエンチャント本", 11
    ),
    _enchanted_book(
        "sr_soul_speed", "SR", "soul_speed", 3, "ソウルスピードIIIのエンチャント本", 11
    ),
    _enchanted_book("sr_density", "SR", "density", 5, "重撃Vのエンチャント本", 11),
    _enchanted_book("sr_breach", "SR", "breach", 4, "防具貫通IVのエンチャント本", 11),
    _enchanted_book("sr_lunge", "SR", "lunge", 3, "突進IIIのエンチャント本", 11),
    ItemGachaReward(
        "ssr_netherite", "SSR", "minecraft:netherite_ingot", "ネザライトインゴット", 1, 10
    ),
    ItemGachaReward("ssr_elytra", "SSR", "minecraft:elytra", "エリトラ", 1, 9),
    ItemGachaReward("ssr_beacon", "SSR", "minecraft:beacon", "ビーコン", 1, 9),
    ItemGachaReward(
        "ssr_enchanted_apple",
        "SSR",
        "minecraft:enchanted_golden_apple",
        "エンチャントされた金のリンゴ",
        2,
        9,
    ),
    ItemGachaReward(
        "ssr_diamond_blocks",
        "SSR",
        "minecraft:diamond_block",
        "ダイヤモンドブロック",
        3,
        9,
    ),
    ItemGachaReward(
        "ur_bow",
        "SSR",
        "minecraft:bow[enchantments={power:5,punch:2,flame:1,infinity:1,unbreaking:3}]",
        "最大強化の弓",
        1,
        10,
    ),
    ItemGachaReward("sr_heavy_core", "SSR", "minecraft:heavy_core", "ヘビーコア", 1, 9),
    ItemGachaReward("sr_dragon_head", "SSR", "minecraft:dragon_head", "ドラゴンの頭", 1, 9),
    ItemGachaReward(
        "sr_silence_trim",
        "SSR",
        "minecraft:silence_armor_trim_smithing_template",
        "鍛冶型: 静寂の装飾",
        1,
        9,
    ),
    ItemGachaReward("ssr_mace", "SSR", "minecraft:mace", "メイス", 1, 9),
    ItemGachaReward(
        "ssr_netherite_spear", "SSR", "minecraft:netherite_spear", "ネザライトの槍", 1, 9
    ),
    ItemGachaReward(
        "ssr_netherite_nautilus_armor",
        "SSR",
        "minecraft:netherite_nautilus_armor",
        "ネザライトのオウムガイの鎧",
        1,
        9,
    ),
    ItemGachaReward("ssr_nether_star", "SSR", "minecraft:nether_star", "ネザースター", 1, 10),
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
        "ur_axe",
        "UR",
        "minecraft:netherite_axe[enchantments={efficiency:5,fortune:3,sharpness:5,unbreaking:3,mending:1}]",
        "最大強化ネザライトの斧",
        1,
        6,
    ),
    ItemGachaReward(
        "ur_shovel",
        "UR",
        "minecraft:netherite_shovel[enchantments={efficiency:5,silk_touch:1,unbreaking:3,mending:1}]",
        "最大強化ネザライトのシャベル",
        1,
        6,
    ),
    _enchanted_book(
        "ssr_wind_burst", "UR", "wind_burst", 3, "ウィンドバーストIIIのエンチャント本", 6
    ),
    ItemGachaReward(
        "ur_trident",
        "UR",
        "minecraft:trident[enchantments={impaling:5,loyalty:3,channeling:1,unbreaking:3,mending:1}]",
        "最大強化トライデント",
        1,
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
    ItemGachaReward(
        "mythic_mace",
        "MYTHIC",
        "minecraft:mace[enchantments={density:5,wind_burst:3,fire_aspect:2,unbreaking:3,mending:1}]",
        "最大強化メイス",
        1,
        1,
    ),
    ItemGachaReward(
        "mythic_spear",
        "MYTHIC",
        "minecraft:netherite_spear[enchantments={sharpness:5,lunge:3,looting:3,fire_aspect:2,knockback:2,unbreaking:3,mending:1}]",
        "最大強化ネザライトの槍",
        1,
        1,
    ),
    ItemGachaReward(
        "mythic_helmet",
        "MYTHIC",
        "minecraft:netherite_helmet[enchantments={protection:4,respiration:3,aqua_affinity:1,thorns:3,unbreaking:3,mending:1}]",
        "最大強化ネザライトのヘルメット",
        1,
        1,
    ),
    ItemGachaReward(
        "mythic_boots",
        "MYTHIC",
        "minecraft:netherite_boots[enchantments={protection:4,feather_falling:4,depth_strider:3,soul_speed:3,thorns:3,unbreaking:3,mending:1}]",
        "最大強化ネザライトのブーツ",
        1,
        1,
    ),
)

# 抽選済みレコードはreward keyで配達・通知するため、当時の内容を復旧用に保持する。
_LEGACY_ITEM_GACHA_REWARDS = (
    ItemGachaReward(
        "r_ominous_trial_key",
        "R",
        "minecraft:ominous_trial_key",
        "不吉な試練の鍵",
        1,
        0,
    ),
    ItemGachaReward("r_trial_key", "R", "minecraft:trial_key", "試練の鍵", 2, 0),
    ItemGachaReward("n_rocket", "N", "minecraft:firework_rocket", "ロケット花火", 48, 0),
    ItemGachaReward("n_xp_bottle", "N", "minecraft:experience_bottle", "エンチャントの瓶", 24, 0),
    ItemGachaReward("n_golden_carrot", "N", "minecraft:golden_carrot", "金のニンジン", 32, 0),
    ItemGachaReward("n_ender_pearl", "N", "minecraft:ender_pearl", "エンダーパール", 12, 0),
    ItemGachaReward("n_wind_charge", "N", "minecraft:wind_charge", "ウィンドチャージ", 16, 0),
    ItemGachaReward("n_chorus_fruit", "N", "minecraft:chorus_fruit", "コーラスフルーツ", 32, 0),
    ItemGachaReward("n_steak", "N", "minecraft:cooked_beef", "ステーキ", 32, 0),
    ItemGachaReward("n_cooked_porkchop", "N", "minecraft:cooked_porkchop", "焼き豚", 32, 0),
    ItemGachaReward("n_pumpkin_pie", "N", "minecraft:pumpkin_pie", "パンプキンパイ", 32, 0),
    ItemGachaReward("n_honey_bottle", "N", "minecraft:honey_bottle", "ハチミツ入りの瓶", 16, 0),
    ItemGachaReward(
        "n_night_vision_potion",
        "N",
        'minecraft:potion[potion_contents="minecraft:long_night_vision"]',
        "暗視のポーション 8:00",
        3,
        0,
    ),
    ItemGachaReward(
        "n_water_breathing_potion",
        "N",
        'minecraft:potion[potion_contents="minecraft:long_water_breathing"]',
        "水中呼吸のポーション 8:00",
        3,
        0,
    ),
    ItemGachaReward("r_golden_apple", "R", "minecraft:golden_apple", "金のリンゴ", 3, 0),
    ItemGachaReward("r_breeze_rod", "R", "minecraft:breeze_rod", "ブリーズロッド", 8, 0),
    ItemGachaReward(
        "r_fire_resistance_potion",
        "R",
        'minecraft:potion[potion_contents="minecraft:long_fire_resistance"]',
        "耐火のポーション 8:00",
        3,
        0,
    ),
    ItemGachaReward(
        "r_slow_falling_potion",
        "R",
        'minecraft:potion[potion_contents="minecraft:long_slow_falling"]',
        "低速落下のポーション 4:00",
        3,
        0,
    ),
    ItemGachaReward(
        "r_healing_splash_potion",
        "R",
        'minecraft:splash_potion[potion_contents="minecraft:strong_healing"]',
        "治癒のスプラッシュポーション II",
        4,
        0,
    ),
    ItemGachaReward(
        "r_strength_potion",
        "R",
        'minecraft:potion[potion_contents="minecraft:strong_strength"]',
        "力のポーション II",
        3,
        0,
    ),
    ItemGachaReward(
        "r_regeneration_potion",
        "R",
        'minecraft:potion[potion_contents="minecraft:strong_regeneration"]',
        "再生のポーション II",
        3,
        0,
    ),
    ItemGachaReward(
        "sr_harming_lingering_potion",
        "SR",
        'minecraft:lingering_potion[potion_contents="minecraft:strong_harming"]',
        "負傷の残留ポーション II",
        4,
        0,
    ),
    ItemGachaReward(
        "sr_turtle_master_splash_potion",
        "SR",
        'minecraft:splash_potion[potion_contents="minecraft:strong_turtle_master"]',
        "タートルマスターのスプラッシュポーション II",
        3,
        0,
    ),
    ItemGachaReward(
        "sr_wind_charged_lingering_potion",
        "SR",
        'minecraft:lingering_potion[potion_contents="minecraft:wind_charged"]',
        "蓄風の残留ポーション",
        3,
        0,
    ),
    ItemGachaReward(
        "sr_weaving_lingering_potion",
        "SR",
        'minecraft:lingering_potion[potion_contents="minecraft:weaving"]',
        "巣張りの残留ポーション",
        3,
        0,
    ),
    ItemGachaReward("r_shulker_shell", "R", "minecraft:shulker_shell", "シュルカーの殻", 2, 0),
    ItemGachaReward("n_nautilus_shell", "R", "minecraft:nautilus_shell", "オウムガイの殻", 4, 0),
    ItemGachaReward("r_echo_shard", "R", "minecraft:echo_shard", "残響の欠片", 4, 0),
    ItemGachaReward("r_heart_of_the_sea", "R", "minecraft:heart_of_the_sea", "海洋の心", 1, 0),
    ItemGachaReward(
        "r_oozing_splash_potion",
        "R",
        'minecraft:splash_potion[potion_contents="minecraft:oozing"]',
        "滲出のスプラッシュポーション",
        3,
        0,
    ),
)
_REWARDS_BY_KEY = {
    reward.key: reward for reward in (*ITEM_GACHA_REWARDS, *_LEGACY_ITEM_GACHA_REWARDS)
}
_REWARDS_BY_TIER = {
    tier: tuple(reward for reward in ITEM_GACHA_REWARDS if reward.tier == tier)
    for tier in _TIER_ORDER
}
_CATALOG_TOTAL_WEIGHT = sum(reward.weight for reward in ITEM_GACHA_REWARDS)
if (
    _CATALOG_TOTAL_WEIGHT != 1600
    or len(_REWARDS_BY_KEY) != len(ITEM_GACHA_REWARDS) + len(_LEGACY_ITEM_GACHA_REWARDS)
    or any(sum(weights.values()) != _DRAW_TABLE_SIZE for weights in _TIER_WEIGHTS_BY_KIND.values())
):
    raise RuntimeError("item gacha reward table is invalid")


def item_gacha_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(_JST).date().isoformat()


def item_gacha_cost_xp(draw_kind: ItemGachaKind) -> int:
    return {
        "normal": ITEM_GACHA_NORMAL_COST_XP,
        "premium": ITEM_GACHA_PREMIUM_COST_XP,
    }[draw_kind]


def item_gacha_kind_label(draw_kind: ItemGachaKind) -> str:
    return "通常" if draw_kind == "normal" else "R以上確定"


def draw_item_gacha_reward(
    draw_kind: ItemGachaKind = "normal",
    roll: int | None = None,
    reward_roll: int | None = None,
) -> ItemGachaReward:
    try:
        tier_weights = _TIER_WEIGHTS_BY_KIND[draw_kind]
    except KeyError as error:
        raise ValueError("unknown item gacha kind") from error
    selected = secrets.randbelow(_DRAW_TABLE_SIZE) if roll is None else roll
    if not 0 <= selected < _DRAW_TABLE_SIZE:
        raise ValueError("roll is outside the item gacha table")
    cursor = 0
    selected_tier: str | None = None
    for tier in _TIER_ORDER:
        cursor += tier_weights[tier]
        if selected < cursor:
            selected_tier = tier
            break
    if selected_tier is None:
        raise RuntimeError("item gacha tier table did not select a tier")

    candidates = _REWARDS_BY_TIER[selected_tier]
    candidate_total = sum(reward.weight for reward in candidates)
    selected_reward = secrets.randbelow(candidate_total) if reward_roll is None else reward_roll
    if not 0 <= selected_reward < candidate_total:
        raise ValueError("reward_roll is outside the selected item gacha tier")
    cursor = 0
    for reward in candidates:
        cursor += reward.weight
        if selected_reward < cursor:
            return reward
    raise RuntimeError("item gacha reward table did not select a reward")


def get_item_gacha_reward(reward_key: str) -> ItemGachaReward:
    try:
        return _REWARDS_BY_KEY[reward_key]
    except KeyError as error:
        raise ValueError("unknown item gacha reward") from error


def item_gacha_give_command(player_name: str, reward_key: str) -> str:
    if not is_safe_server_player_name(player_name):
        raise ValueError("player_name contains unsafe RCON characters")
    reward = get_item_gacha_reward(reward_key)
    return f"give {player_name} {reward.item_spec} {reward.item_count}"


def item_gacha_tellraw_command(player_name: str, reward_key: str) -> str:
    if not is_safe_server_player_name(player_name):
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
            "連携したMinecraftアカウントで参加中に、"
            f"通常 **{ITEM_GACHA_NORMAL_COST_XP:,} XP**、R以上確定 "
            f"**{ITEM_GACHA_PREMIUM_COST_XP:,} XP**から選べます。\n"
            f"両方を合わせて1日 **{ITEM_GACHA_DAILY_LIMIT}回**までです。\n"
            "何が出るかは受け取るまで秘密。景品はその場でMinecraftへ届きます。"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name=f"通常 · {ITEM_GACHA_NORMAL_COST_XP:,} XP",
        value="`N 55%` `R 28%` `SR 11%`\n`SSR 4%` `UR 1.75%` `幻 0.25%`",
        inline=False,
    )
    embed.add_field(
        name=f"R以上確定 · {ITEM_GACHA_PREMIUM_COST_XP:,} XP",
        value="`R 70%` `SR 20%` `SSR 7%`\n`UR 2.5%` `幻 0.5%`",
        inline=False,
    )
    embed.add_field(
        name="🎮 ゲーム内コマンド",
        value=(
            "スマホ版・Bedrock版: `/gacha` で選択メニューを開く\n"
            "通常: `/gacha normal`\n"
            "R以上確定: `/gacha rare`"
        ),
        inline=False,
    )
    embed.add_field(
        name="料金・更新・通知",
        value=(
            f"1日合計 **{ITEM_GACHA_DAILY_LIMIT}回**・毎日 **日本時間0:00** に更新します。\n"
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
        label="通常 100 XP",
        emoji="🎁",
        style=discord.ButtonStyle.primary,
        custom_id="mc-item-gacha:draw:normal",
    )
    async def normal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_item_gacha_panel(interaction):
            await self.bot.show_minecraft_item_gacha_confirmation(interaction, "normal")

    @discord.ui.button(
        label="R以上確定 1,000 XP",
        emoji="💎",
        style=discord.ButtonStyle.success,
        custom_id="mc-item-gacha:draw:premium",
    )
    async def premium(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self.bot.validate_item_gacha_panel(interaction):
            await self.bot.show_minecraft_item_gacha_confirmation(interaction, "premium")


class MinecraftItemGachaConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        *,
        owner_id: int,
        draw_kind: ItemGachaKind,
        cost_xp: int,
        affordable: bool,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id
        self.draw_kind = draw_kind
        self.cost_xp = cost_xp
        self._operation_lock = asyncio.Lock()
        self._completed = False
        self.confirm.label = f"{cost_xp:,} XPで引く"
        if not affordable:
            self.confirm.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "このガチャを操作できるのは本人だけです。", ephemeral=True
        )
        return False

    @discord.ui.button(label="XPで引く", emoji="🎁", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self._operation_lock.locked() or self._completed:
            await interaction.response.send_message(
                "この抽選は処理中または処理済みです。", ephemeral=True
            )
            return
        async with self._operation_lock:
            self._completed = True
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
            await self.bot.draw_minecraft_item_gacha(
                interaction,
                draw_kind=self.draw_kind,
                expected_cost_xp=self.cost_xp,
                response_ready=True,
            )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self._operation_lock.locked():
            await interaction.response.send_message(
                "抽選処理中のためキャンセルできません。", ephemeral=True
            )
            return
        self._completed = True
        await interaction.response.edit_message(
            content="アイテムガチャをキャンセルしました。", embed=None, view=None
        )


__all__ = [
    "ITEM_GACHA_COST_XP",
    "ITEM_GACHA_DAILY_LIMIT",
    "ITEM_GACHA_NORMAL_COST_XP",
    "ITEM_GACHA_PREMIUM_COST_XP",
    "ITEM_GACHA_REWARDS",
    "ItemGachaKind",
    "ItemGachaReward",
    "MinecraftItemGachaConfirmView",
    "MinecraftItemGachaPanelView",
    "draw_item_gacha_reward",
    "get_item_gacha_reward",
    "item_gacha_cost_xp",
    "item_gacha_day",
    "item_gacha_give_command",
    "item_gacha_kind_label",
    "item_gacha_panel_embed",
    "item_gacha_result_embed",
    "item_gacha_tellraw_command",
    "item_gacha_tier_label",
]
