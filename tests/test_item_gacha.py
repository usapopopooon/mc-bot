import asyncio
import sqlite3
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest

from mc_bot.accounts import AccountStore, MinecraftItemGachaDailyLimitReached
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import (
    MinecraftItemGachaOffer,
    MinecraftItemGachaSpendRequest,
    MinecraftXpWallet,
)
from mc_bot.game_messages import private_tellraw_command
from mc_bot.item_gacha import (
    ITEM_GACHA_COST_XP,
    ITEM_GACHA_DAILY_LIMIT,
    ITEM_GACHA_NORMAL_COST_XP,
    ITEM_GACHA_PREMIUM_COST_XP,
    ITEM_GACHA_REWARDS,
    MinecraftItemGachaConfirmView,
    MinecraftItemGachaPanelView,
    draw_item_gacha_reward,
    get_item_gacha_reward,
    item_gacha_day,
    item_gacha_give_command,
    item_gacha_panel_embed,
    item_gacha_result_embed,
    item_gacha_tellraw_command,
)
from mc_bot.settings import RuntimeSettings


class GachaRcon:
    def __init__(self, give_results: list[str | Exception] | None = None) -> None:
        self.give_results = list(give_results or ["Gave 24 [Iron Ingot] to Steve"])
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("give Steve "):
            result = self.give_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        if command.startswith("tellraw @a "):
            return ""
        if command.startswith("tellraw Steve "):
            return ""
        raise AssertionError(f"unexpected RCON command: {command}")


def _store_with_account(tmp_path) -> tuple[AccountStore, int]:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    return store, account.id


def _reserve(
    store: AccountStore,
    account_id: int,
    *,
    reward_key: str = "n_iron",
    draw_kind: str = "normal",
    draw_id: str | None = None,
):
    reward = get_item_gacha_reward(reward_key)
    return store.reserve_minecraft_item_gacha_draw(
        draw_id=draw_id or str(uuid.uuid4()),
        guild_id=456,
        discord_user_id=123,
        account_id=account_id,
        player_name="Steve",
        draw_day="2026-08-14",
        draw_kind=draw_kind,
        cost_xp=1_000 if draw_kind == "premium" else 100,
        tier=reward.tier,
        reward_key=reward.key,
        item_spec=reward.item_spec,
        item_name=reward.item_name,
        item_count=reward.item_count,
    )


def _bot_with_account(tmp_path):
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    bot._settings = RuntimeSettings(guild_id=456)
    bot._online_exchange_account = AsyncMock(return_value=(account, None))  # type: ignore[method-assign]
    wallet_before = MinecraftXpWallet(total_xp=250, spent_xp=50, available_xp=200)
    wallet_after = MinecraftXpWallet(total_xp=250, spent_xp=150, available_xp=100)
    bot._level_bot_xp.fetch_item_gacha_offer = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftItemGachaOffer(
            cost_xp=ITEM_GACHA_COST_XP,
            normal_cost_xp=ITEM_GACHA_NORMAL_COST_XP,
            premium_cost_xp=ITEM_GACHA_PREMIUM_COST_XP,
            daily_limit=ITEM_GACHA_DAILY_LIMIT,
            wallet=wallet_before,
        )
    )
    bot._level_bot_xp.request_item_gacha_spend = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftItemGachaSpendRequest(
            status="reserved",
            message="予約しました。",
            cost_xp=ITEM_GACHA_COST_XP,
            wallet_before=wallet_before,
            wallet_after=wallet_after,
        )
    )
    bot._level_bot_xp.update_item_gacha_spend = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    channel = SimpleNamespace(send=AsyncMock())
    bot._channel = channel  # type: ignore[assignment]
    return bot, account, channel


def _interaction():
    return SimpleNamespace(
        guild_id=456,
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_reward_table_has_exact_published_tier_probabilities() -> None:
    normal = [draw_item_gacha_reward("normal", roll, 0) for roll in range(400)]
    premium = [draw_item_gacha_reward("premium", roll, 0) for roll in range(400)]

    assert Counter(reward.tier for reward in normal) == {
        "N": 220,
        "R": 112,
        "SR": 44,
        "SSR": 16,
        "UR": 7,
        "MYTHIC": 1,
    }
    assert Counter(reward.tier for reward in premium) == {
        "R": 280,
        "SR": 80,
        "SSR": 28,
        "UR": 10,
        "MYTHIC": 2,
    }
    with pytest.raises(ValueError):
        draw_item_gacha_reward("normal", -1)
    with pytest.raises(ValueError):
        draw_item_gacha_reward("normal", 400)
    with pytest.raises(ValueError):
        draw_item_gacha_reward("invalid")  # type: ignore[arg-type]


def test_reward_table_has_no_per_item_rarity_inversion() -> None:
    tier_order = ("N", "R", "SR", "SSR", "UR", "MYTHIC")
    weights_by_tier = {
        tier: [reward.weight for reward in ITEM_GACHA_REWARDS if reward.tier == tier]
        for tier in tier_order
    }

    average_weights = [
        sum(weights_by_tier[tier]) / len(weights_by_tier[tier]) for tier in tier_order
    ]
    assert all(common > rare for common, rare in pairwise(average_weights))
    assert all(
        max(weights_by_tier[rare]) <= min(weights_by_tier[common])
        for common, rare in pairwise(tier_order)
    )


def test_reward_table_has_all_non_curse_max_level_enchantment_books() -> None:
    expected_by_tier = {
        "N": {
            "n_bane": ("bane_of_arthropods", 5, "虫特効Vのエンチャント本"),
            "n_blast_protection": ("blast_protection", 4, "爆発耐性IVのエンチャント本"),
            "n_channeling": ("channeling", 1, "召雷のエンチャント本"),
            "n_fire_protection": ("fire_protection", 4, "火炎耐性IVのエンチャント本"),
            "n_flame": ("flame", 1, "フレイムのエンチャント本"),
            "n_frost_walker": ("frost_walker", 2, "氷渡りIIのエンチャント本"),
            "n_impaling": ("impaling", 5, "水生特効Vのエンチャント本"),
            "n_knockback": (
                "knockback",
                2,
                "ノックバックIIのエンチャント本",  # noqa: RUF001 - Minecraft公式の日本語名
            ),
            "n_loyalty": ("loyalty", 3, "忠誠IIIのエンチャント本"),
            "n_multishot": ("multishot", 1, "拡散のエンチャント本"),
            "n_piercing": ("piercing", 4, "貫通IVのエンチャント本"),
            "n_projectile_protection": (
                "projectile_protection",
                4,
                "飛び道具耐性IVのエンチャント本",
            ),
            "n_punch": ("punch", 2, "パンチIIのエンチャント本"),
            "n_quick_charge": ("quick_charge", 3, "高速装填IIIのエンチャント本"),
            "n_thorns": ("thorns", 3, "棘の鎧IIIのエンチャント本"),
        },
        "R": {
            "r_silk_touch": ("silk_touch", 1, "シルクタッチのエンチャント本"),
            "r_feather_falling": ("feather_falling", 4, "落下耐性IVのエンチャント本"),
            "r_looting": ("looting", 3, "ドロップ増加IIIのエンチャント本"),
            "r_power": ("power", 5, "射撃ダメージ増加Vのエンチャント本"),
            "r_infinity": ("infinity", 1, "無限のエンチャント本"),
            "r_depth_strider": ("depth_strider", 3, "水中歩行IIIのエンチャント本"),
            "r_respiration": ("respiration", 3, "水中呼吸IIIのエンチャント本"),
            "r_aqua_affinity": ("aqua_affinity", 1, "水中採掘のエンチャント本"),
            "r_luck_of_the_sea": ("luck_of_the_sea", 3, "宝釣りIIIのエンチャント本"),
            "r_lure": ("lure", 3, "入れ食いIIIのエンチャント本"),
            "r_fire_aspect": ("fire_aspect", 2, "火属性IIのエンチャント本"),
            "r_smite": ("smite", 5, "アンデッド特効Vのエンチャント本"),
            "r_sweeping_edge": ("sweeping_edge", 3, "範囲ダメージ増加IIIのエンチャント本"),
            "r_riptide": ("riptide", 3, "激流IIIのエンチャント本"),
        },
        "SR": {
            "r_mending": ("mending", 1, "修繕のエンチャント本"),
            "r_fortune": ("fortune", 3, "幸運IIIのエンチャント本"),
            "r_efficiency": ("efficiency", 5, "効率強化Vのエンチャント本"),
            "r_unbreaking": ("unbreaking", 3, "耐久力IIIのエンチャント本"),
            "r_protection": ("protection", 4, "ダメージ軽減IVのエンチャント本"),
            "r_sharpness": ("sharpness", 5, "ダメージ増加Vのエンチャント本"),
            "sr_swift_sneak": ("swift_sneak", 3, "スニーク速度上昇IIIのエンチャント本"),
            "sr_soul_speed": ("soul_speed", 3, "ソウルスピードIIIのエンチャント本"),
            "sr_density": ("density", 5, "重撃Vのエンチャント本"),
            "sr_breach": ("breach", 4, "防具貫通IVのエンチャント本"),
            "sr_lunge": ("lunge", 3, "突進IIIのエンチャント本"),
        },
        "UR": {
            "ssr_wind_burst": ("wind_burst", 3, "ウィンドバーストIIIのエンチャント本"),
        },
    }
    expected = {
        key: (tier, enchantment, level, item_name)
        for tier, rewards in expected_by_tier.items()
        for key, (enchantment, level, item_name) in rewards.items()
    }
    actual = {
        reward.key: reward
        for reward in ITEM_GACHA_REWARDS
        if reward.item_spec.startswith("minecraft:enchanted_book[")
    }

    assert set(actual) == set(expected)
    assert Counter(reward.tier for reward in actual.values()) == {
        "N": 15,
        "R": 14,
        "SR": 11,
        "UR": 1,
    }
    for key, (tier, enchantment, level, item_name) in expected.items():
        reward = actual[key]
        assert reward.tier == tier
        assert reward.item_spec == (
            f"minecraft:enchanted_book[stored_enchantments={{{enchantment}:{level}}}]"
        )
        assert reward.item_name == item_name
        assert reward.item_count == 1

    assert all("binding_curse" not in reward.item_spec for reward in actual.values())
    assert all("vanishing_curse" not in reward.item_spec for reward in actual.values())


def test_reward_table_uses_current_nontrivial_rewards() -> None:
    item_ids = {reward.item_spec.split("[", 1)[0] for reward in ITEM_GACHA_REWARDS}

    assert len(ITEM_GACHA_REWARDS) == 152
    assert Counter(reward.tier for reward in ITEM_GACHA_REWARDS) == {
        "N": 46,
        "R": 49,
        "SR": 28,
        "SSR": 13,
        "UR": 8,
        "MYTHIC": 8,
    }
    assert {"minecraft:name_tag", "minecraft:sponge", "minecraft:saddle"}.isdisjoint(item_ids)
    assert {
        "minecraft:breeze_rod",
        "minecraft:wither_skeleton_skull",
        "minecraft:ominous_trial_key",
        "minecraft:heavy_core",
    } <= item_ids
    assert {
        "minecraft:sulfur",
        "minecraft:cinnabar",
        "minecraft:sulfur_cube_bucket",
        "minecraft:diamond_spear",
        "minecraft:netherite_spear",
        "minecraft:diamond_nautilus_armor",
        "minecraft:netherite_nautilus_armor",
    } <= item_ids
    assert {
        "minecraft:recovery_compass",
        "minecraft:sniffer_egg",
        "minecraft:music_disc_creator",
        "minecraft:silence_armor_trim_smithing_template",
        "minecraft:dragon_head",
        "minecraft:conduit",
    } <= item_ids
    assert get_item_gacha_reward("r_breeze_rod").item_count == 8
    assert get_item_gacha_reward("r_wither_skull").item_count == 2
    assert get_item_gacha_reward("r_wither_skull").tier == "SR"
    assert get_item_gacha_reward("r_ominous_trial_key").item_count == 1
    assert get_item_gacha_reward("sr_heavy_core").item_count == 1
    assert get_item_gacha_reward("sr_heavy_core").tier == "SSR"
    assert get_item_gacha_reward("sr_dragon_head").tier == "SSR"
    assert get_item_gacha_reward("sr_silence_trim").tier == "SSR"
    assert get_item_gacha_reward("ssr_wind_burst").tier == "UR"
    assert get_item_gacha_reward("ur_bow").tier == "SSR"
    assert get_item_gacha_reward("r_diamond_spear").tier == "N"
    assert get_item_gacha_reward("r_sulfur_cube_bucket").tier == "N"
    assert get_item_gacha_reward("n_nautilus_shell").tier == "R"
    assert get_item_gacha_reward("sr_music_creator_box").tier == "R"
    assert get_item_gacha_reward("n_redstone").item_name == "レッドストーンダスト"
    assert get_item_gacha_reward("n_sulfur").item_name == "硫黄"
    assert get_item_gacha_reward("r_music_5").item_name == "レコード: Samuel Åberg - 5"


def test_reward_table_has_food_and_potions_for_different_uses() -> None:
    expected = {
        "n_steak": ("N", "minecraft:cooked_beef", "ステーキ", 32),
        "n_cooked_porkchop": ("N", "minecraft:cooked_porkchop", "焼き豚", 32),
        "n_pumpkin_pie": ("N", "minecraft:pumpkin_pie", "パンプキンパイ", 32),
        "n_honey_bottle": ("N", "minecraft:honey_bottle", "ハチミツ入りの瓶", 16),
        "n_cookie": ("N", "minecraft:cookie", "クッキー", 64),
        "n_night_vision_potion": (
            "N",
            'minecraft:potion[potion_contents="minecraft:long_night_vision"]',
            "暗視のポーション 8:00",
            3,
        ),
        "n_water_breathing_potion": (
            "N",
            'minecraft:potion[potion_contents="minecraft:long_water_breathing"]',
            "水中呼吸のポーション 8:00",
            3,
        ),
        "r_fire_resistance_potion": (
            "R",
            'minecraft:potion[potion_contents="minecraft:long_fire_resistance"]',
            "耐火のポーション 8:00",
            3,
        ),
        "r_slow_falling_potion": (
            "R",
            'minecraft:potion[potion_contents="minecraft:long_slow_falling"]',
            "低速落下のポーション 4:00",
            3,
        ),
        "r_healing_splash_potion": (
            "R",
            'minecraft:splash_potion[potion_contents="minecraft:strong_healing"]',
            "治癒のスプラッシュポーション II",
            4,
        ),
        "r_strength_potion": (
            "R",
            'minecraft:potion[potion_contents="minecraft:strong_strength"]',
            "力のポーション II",
            3,
        ),
        "r_regeneration_potion": (
            "R",
            'minecraft:potion[potion_contents="minecraft:strong_regeneration"]',
            "再生のポーション II",
            3,
        ),
        "r_oozing_splash_potion": (
            "R",
            'minecraft:splash_potion[potion_contents="minecraft:oozing"]',
            "滲出のスプラッシュポーション",
            3,
        ),
        "sr_harming_lingering_potion": (
            "SR",
            'minecraft:lingering_potion[potion_contents="minecraft:strong_harming"]',
            "負傷の残留ポーション II",
            4,
        ),
        "sr_turtle_master_splash_potion": (
            "SR",
            'minecraft:splash_potion[potion_contents="minecraft:strong_turtle_master"]',
            "タートルマスターのスプラッシュポーション II",
            3,
        ),
        "sr_wind_charged_lingering_potion": (
            "SR",
            'minecraft:lingering_potion[potion_contents="minecraft:wind_charged"]',
            "蓄風の残留ポーション",
            3,
        ),
        "sr_weaving_lingering_potion": (
            "SR",
            'minecraft:lingering_potion[potion_contents="minecraft:weaving"]',
            "巣張りの残留ポーション",
            3,
        ),
    }

    assert Counter(tier for tier, *_ in expected.values()) == {"N": 7, "R": 6, "SR": 4}
    for key, (tier, item_spec, item_name, item_count) in expected.items():
        reward = get_item_gacha_reward(key)
        assert (reward.tier, reward.item_spec, reward.item_name, reward.item_count) == (
            tier,
            item_spec,
            item_name,
            item_count,
        )


def test_reward_table_has_every_current_smithing_template() -> None:
    actual = {
        reward.item_spec.removeprefix("minecraft:")
        for reward in ITEM_GACHA_REWARDS
        if reward.item_spec.endswith("smithing_template")
    }

    assert actual == {
        "bolt_armor_trim_smithing_template",
        "coast_armor_trim_smithing_template",
        "dune_armor_trim_smithing_template",
        "eye_armor_trim_smithing_template",
        "flow_armor_trim_smithing_template",
        "host_armor_trim_smithing_template",
        "netherite_upgrade_smithing_template",
        "raiser_armor_trim_smithing_template",
        "rib_armor_trim_smithing_template",
        "sentry_armor_trim_smithing_template",
        "shaper_armor_trim_smithing_template",
        "silence_armor_trim_smithing_template",
        "snout_armor_trim_smithing_template",
        "spire_armor_trim_smithing_template",
        "tide_armor_trim_smithing_template",
        "vex_armor_trim_smithing_template",
        "ward_armor_trim_smithing_template",
        "wayfinder_armor_trim_smithing_template",
        "wild_armor_trim_smithing_template",
    }


def test_joke_rewards_use_real_survival_items() -> None:
    assert get_item_gacha_reward("n_poisonous_potato").item_spec == "minecraft:poisonous_potato"
    assert get_item_gacha_reward("n_dead_bush").item_spec == "minecraft:dead_bush"
    assert get_item_gacha_reward("n_legendary_dirt").item_spec == "minecraft:dirt"
    assert all(reward.key != "n_obsidian_boat" for reward in ITEM_GACHA_REWARDS)
    assert all(reward.item_spec != "minecraft:dark_oak_boat" for reward in ITEM_GACHA_REWARDS)


def test_panel_publishes_only_tier_rates_and_keeps_rewards_secret() -> None:
    embed = item_gacha_panel_embed()
    rendered = str(embed.to_dict())

    assert "N 55%" in rendered
    assert "幻 0.25%" in rendered
    assert "R 70%" in rendered
    assert "100 XP" in rendered
    assert "1,000 XP" in rendered
    assert "1日 **3回**" in rendered
    assert "日本時間0:00" in rendered
    command_field = next(field for field in embed.fields if field.name == "🎮 ゲーム内コマンド")
    assert "`/gacha`" in str(command_field.value)
    assert "`/gacha normal`" in str(command_field.value)
    assert "`/gacha rare`" in str(command_field.value)
    for reward in ITEM_GACHA_REWARDS:
        assert reward.item_name not in rendered
        assert reward.item_spec not in rendered

    async def build_view() -> MinecraftItemGachaPanelView:
        return MinecraftItemGachaPanelView(MinecraftDiscordBot(Config(discord_token="test")))

    view = asyncio.run(build_view())
    assert view.timeout is None
    assert [child.custom_id for child in view.children] == [
        "mc-item-gacha:draw:normal",
        "mc-item-gacha:draw:premium",
    ]


def test_confirmation_shows_balance_and_disables_unaffordable_draw(tmp_path) -> None:
    bot, _, _ = _bot_with_account(tmp_path)
    interaction = _interaction()

    asyncio.run(bot.show_minecraft_item_gacha_confirmation(interaction, "normal"))  # type: ignore[arg-type]

    bot._level_bot_xp.fetch_item_gacha_offer.assert_awaited_once_with(456, 123)  # type: ignore[attr-defined]
    sent = interaction.followup.send.await_args.kwargs
    assert sent["ephemeral"] is True
    assert sent["embed"].title == "通常ガチャの確認"
    assert "200 XP" in str(sent["embed"].to_dict())
    assert "100 XP" in str(sent["embed"].to_dict())
    assert isinstance(sent["view"], MinecraftItemGachaConfirmView)
    assert not sent["view"].confirm.disabled

    bot._level_bot_xp.fetch_item_gacha_offer.return_value = MinecraftItemGachaOffer(  # type: ignore[attr-defined]
        cost_xp=ITEM_GACHA_COST_XP,
        normal_cost_xp=ITEM_GACHA_NORMAL_COST_XP,
        premium_cost_xp=ITEM_GACHA_PREMIUM_COST_XP,
        daily_limit=ITEM_GACHA_DAILY_LIMIT,
        wallet=MinecraftXpWallet(total_xp=99, spent_xp=0, available_xp=99),
    )
    insufficient = _interaction()
    asyncio.run(bot.show_minecraft_item_gacha_confirmation(insufficient, "normal"))  # type: ignore[arg-type]
    insufficient_view = insufficient.followup.send.await_args.kwargs["view"]
    assert insufficient_view.confirm.disabled


def test_confirmation_keeps_an_unfinished_draw_retryable_with_low_available_xp(
    tmp_path,
) -> None:
    bot, account, _ = _bot_with_account(tmp_path)
    reward = get_item_gacha_reward("n_iron")
    draw, created = bot._accounts.reserve_minecraft_item_gacha_draw(
        draw_id=str(uuid.uuid4()),
        guild_id=456,
        discord_user_id=123,
        account_id=account.id,
        player_name="Steve",
        draw_day=item_gacha_day(datetime.now(UTC)),
        draw_kind="normal",
        cost_xp=100,
        tier=reward.tier,
        reward_key=reward.key,
        item_spec=reward.item_spec,
        item_name=reward.item_name,
        item_count=reward.item_count,
    )
    assert created and draw.status == "reserved"
    bot._level_bot_xp.fetch_item_gacha_offer.return_value = MinecraftItemGachaOffer(  # type: ignore[attr-defined]
        cost_xp=100,
        normal_cost_xp=100,
        premium_cost_xp=1_000,
        daily_limit=3,
        wallet=MinecraftXpWallet(total_xp=100, spent_xp=100, available_xp=0),
    )
    interaction = _interaction()

    asyncio.run(bot.show_minecraft_item_gacha_confirmation(interaction, "premium"))  # type: ignore[arg-type]

    sent = interaction.followup.send.await_args.kwargs
    rendered = str(sent["embed"].to_dict())
    assert sent["embed"].title == "通常ガチャの確認"
    assert "未完了" in rendered
    assert "決済状態を再確認" in rendered
    assert not sent["view"].confirm.disabled


def test_panel_and_confirmation_buttons_wire_the_confirmed_price(tmp_path) -> None:
    bot, _, _ = _bot_with_account(tmp_path)
    bot.validate_item_gacha_panel = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bot.show_minecraft_item_gacha_confirmation = AsyncMock()  # type: ignore[method-assign]
    bot.draw_minecraft_item_gacha = AsyncMock()  # type: ignore[method-assign]
    interaction = _interaction()
    interaction.response.edit_message = AsyncMock()

    async def exercise() -> None:
        panel = MinecraftItemGachaPanelView(bot)
        await panel.normal.callback(interaction)  # type: ignore[arg-type]
        await panel.premium.callback(interaction)  # type: ignore[arg-type]
        confirmation = MinecraftItemGachaConfirmView(
            bot,
            owner_id=123,
            draw_kind="normal",
            cost_xp=100,
            affordable=True,
        )
        await confirmation.confirm.callback(interaction)  # type: ignore[arg-type]

    asyncio.run(exercise())

    assert bot.validate_item_gacha_panel.await_count == 2  # type: ignore[attr-defined]
    bot.validate_item_gacha_panel.assert_has_awaits(  # type: ignore[attr-defined]
        [call(interaction), call(interaction)]
    )
    assert bot.show_minecraft_item_gacha_confirmation.await_args_list == [  # type: ignore[attr-defined]
        call(interaction, "normal"),
        call(interaction, "premium"),
    ]
    interaction.response.edit_message.assert_awaited_once()
    bot.draw_minecraft_item_gacha.assert_awaited_once_with(  # type: ignore[attr-defined]
        interaction,
        draw_kind="normal",
        expected_cost_xp=100,
        response_ready=True,
    )


def test_gacha_day_resets_at_midnight_in_japan() -> None:
    assert item_gacha_day(datetime(2026, 8, 13, 14, 59, 59, tzinfo=UTC)) == "2026-08-13"
    assert item_gacha_day(datetime(2026, 8, 13, 15, 0, 0, tzinfo=UTC)) == "2026-08-14"
    with pytest.raises(ValueError):
        item_gacha_day(datetime(2026, 8, 14, 5, 0, 0))


def test_commands_use_only_catalog_rewards_and_safe_player_names() -> None:
    assert item_gacha_give_command("Steve", "n_iron") == ("give Steve minecraft:iron_ingot 24")
    assert "stored_enchantments={mending:1}" in item_gacha_give_command("Steve", "r_mending")
    assert "enchantments={sharpness:5" in item_gacha_give_command("Steve", "mythic_sword")
    assert item_gacha_give_command("Steve", "r_diamond_spear") == (
        "give Steve minecraft:diamond_spear 1"
    )
    assert item_gacha_give_command("Steve", "r_healing_splash_potion") == (
        'give Steve minecraft:splash_potion[potion_contents="minecraft:strong_healing"] 4'
    )
    assert item_gacha_give_command("*Steve", "n_iron") == ("give *Steve minecraft:iron_ingot 24")
    assert "lunge:3" in item_gacha_give_command("Steve", "mythic_spear")
    tellraw = item_gacha_tellraw_command("Steve", "n_iron")
    assert tellraw.startswith("tellraw @a ")
    assert "【N】" in tellraw
    assert "鉄インゴット x24" in tellraw
    with pytest.raises(ValueError):
        item_gacha_give_command("@a", "n_iron")
    with pytest.raises(ValueError):
        item_gacha_give_command("Steve", "unknown")


def test_private_game_response_targets_only_the_requesting_player() -> None:
    command = private_tellraw_command(
        "Steve",
        "受け取りました: **【R】ダイヤモンド x3**\n本日 1/3回",
    )

    assert command.startswith('tellraw Steve {"text":')
    assert "@a" not in command
    assert "**" not in command
    assert "本日 1/3回" in command
    assert private_tellraw_command("*Steve", "完了").startswith('tellraw *Steve {"text":')
    with pytest.raises(ValueError):
        private_tellraw_command("@a", "だめ")


def test_result_embed_uses_minecraft_name_and_discord_mention() -> None:
    embed = item_gacha_result_embed(
        player_name="*Steve*",
        discord_user_id=123,
        reward_key="n_iron",
    )

    assert embed.title == "🎁 アイテムガチャ【N】"
    assert r"**\*Steve\* (<@123>) さん** が" in str(embed.description)
    assert "鉄インゴット x24" in str(embed.description)


def test_store_reuses_incomplete_draw_and_allows_three_completed_draws_per_day(
    tmp_path,
) -> None:
    store, account_id = _store_with_account(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: _reserve(store, account_id), range(16)))

    assert sum(created for _, created in results) == 1
    assert len({draw.draw_id for draw, _ in results}) == 1
    saved = store.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day="2026-08-14",
    )
    assert saved is not None
    assert saved.reward_key == "n_iron"
    assert saved.status == "reserved"
    assert saved.draw_number == 1
    assert saved.draw_kind == "normal"
    assert saved.cost_xp == 100

    for expected_number in (2, 3):
        store.mark_minecraft_item_gacha_status(saved.draw_id, "delivered")
        saved, created = _reserve(store, account_id)
        assert created
        assert saved.draw_number == expected_number

    store.mark_minecraft_item_gacha_status(saved.draw_id, "delivered")
    with pytest.raises(MinecraftItemGachaDailyLimitReached):
        _reserve(store, account_id)
    assert (
        store.count_minecraft_item_gacha_draws(
            guild_id=456, discord_user_id=123, draw_day="2026-08-14"
        )
        == 3
    )


def test_store_replays_the_same_request_id_without_a_second_draw(tmp_path) -> None:
    store, account_id = _store_with_account(tmp_path)
    request_id = "11111111-1111-4111-8111-111111111111"
    first, created = _reserve(store, account_id, draw_id=request_id)
    assert created
    store.mark_minecraft_item_gacha_status(first.draw_id, "delivered")

    replayed, created = _reserve(
        store,
        account_id,
        draw_id=request_id,
        reward_key="r_diamond",
    )

    assert not created
    assert replayed.draw_id == first.draw_id
    assert replayed.reward_key == "n_iron"
    assert replayed.status == "delivered"
    assert (
        store.count_minecraft_item_gacha_draws(
            guild_id=456,
            discord_user_id=123,
            draw_day="2026-08-14",
        )
        == 1
    )
    reward = get_item_gacha_reward("n_iron")
    with pytest.raises(ValueError, match="request ID was reused"):
        store.reserve_minecraft_item_gacha_draw(
            draw_id=request_id,
            guild_id=456,
            discord_user_id=999,
            account_id=account_id,
            player_name="Steve",
            draw_day="2026-08-14",
            draw_kind="normal",
            cost_xp=100,
            tier=reward.tier,
            reward_key=reward.key,
            item_spec=reward.item_spec,
            item_name=reward.item_name,
            item_count=reward.item_count,
        )


def test_store_tracks_delivery_and_each_public_notification(tmp_path) -> None:
    store, account_id = _store_with_account(tmp_path)
    draw, created = _reserve(store, account_id)
    assert created
    assert not store.has_pending_minecraft_item_gacha_notifications(guild_id=456)

    store.mark_minecraft_item_gacha_status(draw.draw_id, "delivered")
    assert store.has_pending_minecraft_item_gacha_notifications(guild_id=456)
    assert not store.has_pending_minecraft_item_gacha_notifications(guild_id=789)
    pending = store.list_pending_minecraft_item_gacha_notifications()
    assert [item.draw_id for item in pending] == [draw.draw_id]

    store.mark_minecraft_item_gacha_notified(draw.draw_id, "minecraft")
    assert store.has_pending_minecraft_item_gacha_notifications(guild_id=456)
    store.mark_minecraft_item_gacha_notified(draw.draw_id, "discord")

    assert not store.has_pending_minecraft_item_gacha_notifications(guild_id=456)
    assert store.list_pending_minecraft_item_gacha_notifications() == []
    completed = store.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day="2026-08-14",
    )
    assert completed is not None
    assert completed.status == "delivered"
    assert completed.minecraft_notified
    assert completed.discord_notified


def test_recovery_runs_only_when_a_notification_is_pending(tmp_path) -> None:
    bot, account, _ = _bot_with_account(tmp_path)
    bot._flush_minecraft_item_gacha_notifications = AsyncMock()  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._recover_minecraft_item_gacha_notifications(456)
        bot._flush_minecraft_item_gacha_notifications.assert_not_awaited()  # type: ignore[attr-defined]

        draw, created = _reserve(bot._accounts, account.id)
        assert created
        bot._accounts.mark_minecraft_item_gacha_status(draw.draw_id, "delivered")
        await bot._recover_minecraft_item_gacha_notifications(456)

    asyncio.run(exercise())

    bot._flush_minecraft_item_gacha_notifications.assert_awaited_once_with(456)  # type: ignore[attr-defined]


def test_notification_recovery_stops_after_five_attempts_per_destination(tmp_path) -> None:
    bot, account, channel = _bot_with_account(tmp_path)
    bot._execute_checked_rcon = AsyncMock(  # type: ignore[method-assign]
        side_effect=OSError("Minecraft unavailable")
    )
    channel.send.side_effect = RuntimeError("Discord unavailable")
    draw, created = _reserve(bot._accounts, account.id)
    assert created
    bot._accounts.mark_minecraft_item_gacha_status(draw.draw_id, "delivered")

    async def exercise() -> None:
        for _ in range(10):
            await bot._recover_minecraft_item_gacha_notifications(456)

    asyncio.run(exercise())

    assert bot._execute_checked_rcon.await_count == 5  # type: ignore[attr-defined]
    assert channel.send.await_count == 5
    reopened = AccountStore(tmp_path / "accounts.db")
    reopened.initialize()
    saved = reopened.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day="2026-08-14",
    )
    assert saved is not None
    assert saved.minecraft_notification_attempts == 5
    assert saved.discord_notification_attempts == 5
    assert not reopened.has_pending_minecraft_item_gacha_notifications(guild_id=456)


def test_exhausted_minecraft_notification_does_not_block_discord(tmp_path) -> None:
    bot, account, channel = _bot_with_account(tmp_path)
    bot._execute_checked_rcon = AsyncMock()  # type: ignore[method-assign]
    draw, created = _reserve(bot._accounts, account.id)
    assert created
    bot._accounts.mark_minecraft_item_gacha_status(draw.draw_id, "delivered")
    for _ in range(5):
        bot._accounts.begin_minecraft_item_gacha_notification_attempt(
            draw.draw_id,
            "minecraft",
        )

    asyncio.run(bot._recover_minecraft_item_gacha_notifications(456))

    bot._execute_checked_rcon.assert_not_awaited()  # type: ignore[attr-defined]
    channel.send.assert_awaited_once()
    saved = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day="2026-08-14",
    )
    assert saved is not None
    assert not saved.minecraft_notified
    assert saved.minecraft_notification_attempts == 5
    assert saved.discord_notified
    assert saved.discord_notification_attempts == 1
    assert not bot._accounts.has_pending_minecraft_item_gacha_notifications(guild_id=456)


def test_concurrent_notification_flush_sends_each_destination_once(tmp_path) -> None:
    bot, account, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    draw, created = _reserve(bot._accounts, account.id)
    assert created
    bot._accounts.mark_minecraft_item_gacha_status(draw.draw_id, "delivered")

    async def exercise() -> None:
        await asyncio.gather(
            bot._flush_minecraft_item_gacha_notifications(456),
            bot._flush_minecraft_item_gacha_notifications(456),
        )

    asyncio.run(exercise())

    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 1
    channel.send.assert_awaited_once()


def test_three_daily_draws_deliver_and_fourth_is_rejected(tmp_path) -> None:
    bot, account, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon(["Gave 24 [Iron Ingot] to Steve"] * 3)
    bot._rcon = rcon  # type: ignore[assignment]
    interactions = [_interaction() for _ in range(4)]
    reward = get_item_gacha_reward("n_iron")

    async def exercise() -> None:
        with patch("mc_bot.bot.draw_item_gacha_reward", return_value=reward):
            for interaction in interactions:
                await bot.draw_minecraft_item_gacha(interaction)  # type: ignore[arg-type]

    asyncio.run(exercise())

    assert rcon.commands.count("give Steve minecraft:iron_ingot 24") == 3
    public_commands = [command for command in rcon.commands if command.startswith("tellraw @a ")]
    assert len(public_commands) == 3
    assert all("【N】" in command for command in public_commands)
    assert channel.send.await_count == 3
    log = channel.send.await_args.kwargs
    assert log["content"] == "<@123>"
    assert log["allowed_mentions"].everyone is False
    assert log["allowed_mentions"].roles is False
    assert [user.id for user in log["allowed_mentions"].users] == [123]
    assert "【N】" in log["embed"].title
    assert "**Steve (<@123>) さん** が" in str(log["embed"].description)
    assert "鉄インゴット x24" in str(log["embed"].description)
    for draw_number, interaction in enumerate(interactions[:3], start=1):
        received = interaction.followup.send.await_args.args[0]
        assert "受け取りました" in received
        assert f"本日 {draw_number}/3回" in received
    assert "3回" in interactions[3].followup.send.await_args.args[0]
    draw = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert draw is not None
    assert draw.account_id == account.id
    assert draw.draw_number == 3
    assert draw.draw_kind == "normal"
    assert draw.cost_xp == 100
    assert draw.status == "delivered"
    assert draw.minecraft_notified
    assert draw.discord_notified
    assert bot._level_bot_xp.request_item_gacha_spend.await_count == 3  # type: ignore[attr-defined]
    for spend_call in bot._level_bot_xp.request_item_gacha_spend.await_args_list:  # type: ignore[attr-defined]
        assert spend_call.kwargs["guild_id"] == 456
        assert spend_call.kwargs["user_id"] == 123
        assert spend_call.kwargs["account_id"] == account.id
        assert spend_call.kwargs["draw_day"] == draw.draw_day
        assert spend_call.kwargs["expected_cost_xp"] == 100
    assert bot._level_bot_xp.update_item_gacha_spend.await_count == 3  # type: ignore[attr-defined]
    assert "サーバーXP **100**消費" in interactions[0].followup.send.await_args.args[0]


def test_premium_draw_uses_1000_xp_and_persists_its_kind(tmp_path) -> None:
    bot, account, channel = _bot_with_account(tmp_path)
    bot._rcon = GachaRcon(["Gave 3 [Diamond] to Steve"])  # type: ignore[assignment]
    wallet_before = MinecraftXpWallet(total_xp=2_000, spent_xp=0, available_xp=2_000)
    wallet_after = MinecraftXpWallet(total_xp=2_000, spent_xp=1_000, available_xp=1_000)
    bot._level_bot_xp.request_item_gacha_spend.return_value = (  # type: ignore[attr-defined]
        MinecraftItemGachaSpendRequest(
            status="reserved",
            message="予約しました。",
            cost_xp=1_000,
            wallet_before=wallet_before,
            wallet_after=wallet_after,
        )
    )
    interaction = _interaction()

    async def exercise() -> None:
        with patch(
            "mc_bot.bot.draw_item_gacha_reward",
            return_value=get_item_gacha_reward("r_diamond"),
        ):
            await bot.draw_minecraft_item_gacha(  # type: ignore[arg-type]
                interaction,
                draw_kind="premium",
                expected_cost_xp=1_000,
            )

    asyncio.run(exercise())

    draw = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert draw is not None
    assert draw.account_id == account.id
    assert draw.draw_kind == "premium"
    assert draw.cost_xp == 1_000
    assert draw.tier == "R"
    assert draw.status == "delivered"
    bot._level_bot_xp.request_item_gacha_spend.assert_awaited_once_with(  # type: ignore[attr-defined]
        guild_id=456,
        user_id=123,
        request_id=draw.draw_id,
        account_id=account.id,
        draw_day=draw.draw_day,
        expected_cost_xp=1_000,
    )
    assert "R以上確定" in interaction.followup.send.await_args.args[0]
    assert "**1,000**消費" in interaction.followup.send.await_args.args[0]
    channel.send.assert_awaited_once()


def test_draw_is_retryable_while_payment_is_requested_and_reserved_before_rcon(
    tmp_path,
) -> None:
    bot, _, _ = _bot_with_account(tmp_path)
    interaction = _interaction()
    spend = bot._level_bot_xp.request_item_gacha_spend.return_value  # type: ignore[attr-defined]

    async def request_spend(**_kwargs):
        draw = bot._accounts.get_minecraft_item_gacha_draw(
            guild_id=456,
            discord_user_id=123,
            draw_day=item_gacha_day(datetime.now(UTC)),
        )
        assert draw is not None and draw.status == "retryable"
        return spend

    async def execute(command: str) -> str:
        if command.startswith("give Steve "):
            draw = bot._accounts.get_minecraft_item_gacha_draw(
                guild_id=456,
                discord_user_id=123,
                draw_day=item_gacha_day(datetime.now(UTC)),
            )
            assert draw is not None and draw.status == "reserved"
        return ""

    bot._level_bot_xp.request_item_gacha_spend = AsyncMock(  # type: ignore[method-assign]
        side_effect=request_spend
    )
    bot._execute_checked_rcon = AsyncMock(side_effect=execute)  # type: ignore[method-assign]

    asyncio.run(bot.draw_minecraft_item_gacha(interaction))  # type: ignore[arg-type]

    saved = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert saved is not None and saved.status == "delivered"


def test_explicit_minecraft_rejection_retries_the_same_secret_reward(tmp_path) -> None:
    bot, _, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon(["No player was found", "Gave 24 [Iron Ingot] to Steve"])
    bot._rcon = rcon  # type: ignore[assignment]
    first = _interaction()
    second = _interaction()

    async def exercise() -> None:
        with patch(
            "mc_bot.bot.draw_item_gacha_reward",
            side_effect=[get_item_gacha_reward("n_iron"), get_item_gacha_reward("ssr_elytra")],
        ):
            await bot.draw_minecraft_item_gacha(first)  # type: ignore[arg-type]
            await bot.draw_minecraft_item_gacha(second)  # type: ignore[arg-type]

    asyncio.run(exercise())

    gives = [command for command in rcon.commands if command.startswith("give Steve ")]
    assert gives == ["give Steve minecraft:iron_ingot 24"] * 2
    assert "同じ景品で再試行" in first.followup.send.await_args.args[0]
    assert "受け取りました" in second.followup.send.await_args.args[0]
    channel.send.assert_awaited_once()
    assert bot._level_bot_xp.request_item_gacha_spend.await_count == 2  # type: ignore[attr-defined]
    actions = [
        call.kwargs["action"]
        for call in bot._level_bot_xp.update_item_gacha_spend.await_args_list  # type: ignore[attr-defined]
    ]
    assert actions == ["cancel", "complete"]


def test_ambiguous_delivery_consumes_one_slot_and_next_draw_is_new(tmp_path) -> None:
    bot, _, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon([OSError("RCON response lost"), "Gave 1 [Elytra] to Steve"])
    bot._rcon = rcon  # type: ignore[assignment]
    first = _interaction()
    second = _interaction()

    async def exercise() -> None:
        with patch(
            "mc_bot.bot.draw_item_gacha_reward",
            side_effect=[
                get_item_gacha_reward("n_iron"),
                get_item_gacha_reward("ssr_elytra"),
            ],
        ):
            await bot.draw_minecraft_item_gacha(first)  # type: ignore[arg-type]
            await bot.draw_minecraft_item_gacha(second)  # type: ignore[arg-type]

    asyncio.run(exercise())

    gives = [command for command in rcon.commands if command.startswith("give Steve ")]
    assert gives == [
        "give Steve minecraft:iron_ingot 24",
        "give Steve minecraft:elytra 1",
    ]
    assert "再抽選は行いません" in first.followup.send.await_args.args[0]
    assert "受け取りました" in second.followup.send.await_args.args[0]
    channel.send.assert_awaited_once()
    assert bot._level_bot_xp.request_item_gacha_spend.await_count == 2  # type: ignore[attr-defined]

    with sqlite3.connect(tmp_path / "accounts.db") as connection:
        rows = connection.execute(
            """
            SELECT draw_number, status
            FROM minecraft_item_gacha_draws
            ORDER BY draw_number
            """
        ).fetchall()
    assert rows == [(1, "ambiguous"), (2, "delivered")]


def test_discord_log_failure_retries_only_the_log_not_the_reward(tmp_path) -> None:
    bot, _, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    channel.send.side_effect = RuntimeError("Discord unavailable")
    interaction = _interaction()
    reward = get_item_gacha_reward("n_iron")

    async def exercise() -> None:
        with patch("mc_bot.bot.draw_item_gacha_reward", return_value=reward):
            await bot.draw_minecraft_item_gacha(interaction)  # type: ignore[arg-type]
        channel.send.side_effect = None
        await bot._flush_minecraft_item_gacha_notifications(456)

    asyncio.run(exercise())

    assert rcon.commands.count("give Steve minecraft:iron_ingot 24") == 1
    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 1
    assert channel.send.await_count == 2
    draw = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert draw is not None
    assert draw.minecraft_notified
    assert draw.discord_notified


def test_offline_player_does_not_consume_daily_draw(tmp_path) -> None:
    bot, _, channel = _bot_with_account(tmp_path)
    bot._online_exchange_account = AsyncMock(  # type: ignore[method-assign]
        return_value=(None, "player_offline")
    )
    interaction = _interaction()

    asyncio.run(bot.draw_minecraft_item_gacha(interaction))  # type: ignore[arg-type]

    with sqlite3.connect(tmp_path / "accounts.db") as connection:
        count = connection.execute("SELECT COUNT(*) FROM minecraft_item_gacha_draws").fetchone()[0]
    assert count == 0
    assert "サーバーに参加" in interaction.followup.send.await_args.args[0]
    channel.send.assert_not_awaited()
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()  # type: ignore[attr-defined]


def test_insufficient_xp_does_not_deliver_and_can_retry_same_reward(tmp_path) -> None:
    bot, _, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    wallet = MinecraftXpWallet(total_xp=99, spent_xp=0, available_xp=99)
    bot._level_bot_xp.request_item_gacha_spend.return_value = (  # type: ignore[attr-defined]
        MinecraftItemGachaSpendRequest(
            status="insufficient_xp",
            message="XPが 1 XP不足しています。",
            cost_xp=100,
            wallet_before=wallet,
            wallet_after=wallet,
        )
    )
    interaction = _interaction()

    asyncio.run(bot.draw_minecraft_item_gacha(interaction))  # type: ignore[arg-type]

    assert rcon.commands == []
    assert "1 XP不足" in interaction.followup.send.await_args.args[0]
    draw = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert draw is not None and draw.status == "retryable"
    channel.send.assert_not_awaited()


def test_spend_price_mismatch_cancels_without_delivering(tmp_path) -> None:
    bot, _, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    bot._level_bot_xp.request_item_gacha_spend.return_value = (  # type: ignore[attr-defined]
        MinecraftItemGachaSpendRequest(
            status="reserved",
            message="予約しました。",
            cost_xp=1_000,
            wallet_before=MinecraftXpWallet(total_xp=2_000, spent_xp=0, available_xp=2_000),
            wallet_after=MinecraftXpWallet(total_xp=2_000, spent_xp=1_000, available_xp=1_000),
        )
    )
    interaction = _interaction()

    asyncio.run(bot.draw_minecraft_item_gacha(interaction))  # type: ignore[arg-type]

    assert rcon.commands == []
    assert "価格が一致" in interaction.followup.send.await_args.args[0]
    draw = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert draw is not None and draw.status == "retryable"
    bot._level_bot_xp.update_item_gacha_spend.assert_awaited_once_with(  # type: ignore[attr-defined]
        request_id=draw.draw_id,
        guild_id=456,
        user_id=123,
        action="cancel",
    )
    channel.send.assert_not_awaited()
