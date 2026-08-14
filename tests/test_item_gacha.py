import asyncio
import sqlite3
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mc_bot.accounts import AccountStore
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.item_gacha import (
    ITEM_GACHA_REWARDS,
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


def _reserve(store: AccountStore, account_id: int, *, reward_key: str = "n_iron"):
    reward = get_item_gacha_reward(reward_key)
    return store.reserve_minecraft_item_gacha_draw(
        draw_id=str(uuid.uuid4()),
        guild_id=456,
        discord_user_id=123,
        account_id=account_id,
        player_name="Steve",
        draw_day="2026-08-14",
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
    selected = [draw_item_gacha_reward(roll) for roll in range(800)]

    assert Counter(reward.tier for reward in selected) == {
        "N": 280,
        "R": 280,
        "SR": 152,
        "SSR": 60,
        "UR": 24,
        "MYTHIC": 4,
    }
    assert Counter(reward.key for reward in selected) == {
        reward.key: reward.weight for reward in ITEM_GACHA_REWARDS
    }
    with pytest.raises(ValueError):
        draw_item_gacha_reward(-1)
    with pytest.raises(ValueError):
        draw_item_gacha_reward(800)


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
            "r_mending": ("mending", 1, "修繕のエンチャント本"),
            "r_fortune": ("fortune", 3, "幸運IIIのエンチャント本"),
            "r_efficiency": ("efficiency", 5, "効率強化Vのエンチャント本"),
            "r_unbreaking": ("unbreaking", 3, "耐久力IIIのエンチャント本"),
            "r_silk_touch": ("silk_touch", 1, "シルクタッチのエンチャント本"),
            "r_protection": ("protection", 4, "ダメージ軽減IVのエンチャント本"),
            "r_feather_falling": ("feather_falling", 4, "落下耐性IVのエンチャント本"),
            "r_looting": ("looting", 3, "ドロップ増加IIIのエンチャント本"),
            "r_sharpness": ("sharpness", 5, "ダメージ増加Vのエンチャント本"),
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
            "sr_swift_sneak": ("swift_sneak", 3, "スニーク速度上昇IIIのエンチャント本"),
            "sr_soul_speed": ("soul_speed", 3, "ソウルスピードIIIのエンチャント本"),
            "sr_density": ("density", 5, "重撃Vのエンチャント本"),
            "sr_breach": ("breach", 4, "防具貫通IVのエンチャント本"),
            "sr_lunge": ("lunge", 3, "突進IIIのエンチャント本"),
        },
        "SSR": {
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
        "R": 20,
        "SR": 5,
        "SSR": 1,
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

    assert {"minecraft:name_tag", "minecraft:sponge", "minecraft:saddle"}.isdisjoint(item_ids)
    assert {
        "minecraft:breeze_rod",
        "minecraft:wither_skeleton_skull",
        "minecraft:ominous_trial_key",
        "minecraft:heavy_core",
    } <= item_ids
    assert get_item_gacha_reward("r_breeze_rod").item_count == 8
    assert get_item_gacha_reward("r_wither_skull").item_count == 2
    assert get_item_gacha_reward("r_ominous_trial_key").item_count == 1
    assert get_item_gacha_reward("sr_heavy_core").item_count == 1
    assert get_item_gacha_reward("n_redstone").item_name == "レッドストーンダスト"


def test_panel_publishes_only_tier_rates_and_keeps_rewards_secret() -> None:
    embed = item_gacha_panel_embed()
    rendered = str(embed.to_dict())

    assert "N 35%" in rendered
    assert "幻 0.5%" in rendered
    assert "日本時間0:00" in rendered
    for reward in ITEM_GACHA_REWARDS:
        assert reward.item_name not in rendered
        assert reward.item_spec not in rendered

    async def build_view() -> MinecraftItemGachaPanelView:
        return MinecraftItemGachaPanelView(MinecraftDiscordBot(Config(discord_token="test")))

    view = asyncio.run(build_view())
    assert view.timeout is None
    assert [child.custom_id for child in view.children] == ["mc-item-gacha:draw"]


def test_gacha_day_resets_at_midnight_in_japan() -> None:
    assert item_gacha_day(datetime(2026, 8, 13, 14, 59, 59, tzinfo=UTC)) == "2026-08-13"
    assert item_gacha_day(datetime(2026, 8, 13, 15, 0, 0, tzinfo=UTC)) == "2026-08-14"
    with pytest.raises(ValueError):
        item_gacha_day(datetime(2026, 8, 14, 5, 0, 0))


def test_commands_use_only_catalog_rewards_and_safe_player_names() -> None:
    assert item_gacha_give_command("Steve", "n_iron") == ("give Steve minecraft:iron_ingot 24")
    assert "stored_enchantments={mending:1}" in item_gacha_give_command("Steve", "r_mending")
    assert "enchantments={sharpness:5" in item_gacha_give_command("Steve", "mythic_sword")
    tellraw = item_gacha_tellraw_command("Steve", "n_iron")
    assert tellraw.startswith("tellraw @a ")
    assert "【N】" in tellraw
    assert "鉄インゴット x24" in tellraw
    with pytest.raises(ValueError):
        item_gacha_give_command("@a", "n_iron")
    with pytest.raises(ValueError):
        item_gacha_give_command("Steve", "unknown")


def test_result_embed_uses_minecraft_name_and_discord_mention() -> None:
    embed = item_gacha_result_embed(
        player_name="*Steve*",
        discord_user_id=123,
        reward_key="n_iron",
    )

    assert embed.title == "🎁 アイテムガチャ【N】"
    assert r"**\*Steve\* (<@123>) さん** が" in str(embed.description)
    assert "鉄インゴット x24" in str(embed.description)


def test_store_allows_only_one_draw_per_user_guild_and_day(tmp_path) -> None:
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


def test_draw_delivers_once_and_logs_n_to_minecraft_and_discord(tmp_path) -> None:
    bot, account, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    first = _interaction()
    second = _interaction()
    reward = get_item_gacha_reward("n_iron")

    async def exercise() -> None:
        with patch("mc_bot.bot.draw_item_gacha_reward", return_value=reward):
            await bot.draw_minecraft_item_gacha(first)  # type: ignore[arg-type]
            await bot.draw_minecraft_item_gacha(second)  # type: ignore[arg-type]

    asyncio.run(exercise())

    assert rcon.commands.count("give Steve minecraft:iron_ingot 24") == 1
    public_commands = [command for command in rcon.commands if command.startswith("tellraw @a ")]
    assert len(public_commands) == 1
    assert "【N】" in public_commands[0]
    channel.send.assert_awaited_once()
    log = channel.send.await_args.kwargs
    assert log["content"] == "<@123>"
    assert log["allowed_mentions"].everyone is False
    assert log["allowed_mentions"].roles is False
    assert [user.id for user in log["allowed_mentions"].users] == [123]
    assert "【N】" in log["embed"].title
    assert "**Steve (<@123>) さん** が" in str(log["embed"].description)
    assert "鉄インゴット x24" in str(log["embed"].description)
    assert "受け取りました" in first.followup.send.await_args.args[0]
    assert "本日は受取済み" in second.followup.send.await_args.args[0]
    draw = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert draw is not None
    assert draw.account_id == account.id
    assert draw.status == "delivered"
    assert draw.minecraft_notified
    assert draw.discord_notified


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


def test_ambiguous_rcon_delivery_is_never_retried(tmp_path) -> None:
    bot, _, channel = _bot_with_account(tmp_path)
    rcon = GachaRcon([OSError("RCON response lost")])
    bot._rcon = rcon  # type: ignore[assignment]
    first = _interaction()
    second = _interaction()
    reward = get_item_gacha_reward("n_iron")

    async def exercise() -> None:
        with patch("mc_bot.bot.draw_item_gacha_reward", return_value=reward):
            await bot.draw_minecraft_item_gacha(first)  # type: ignore[arg-type]
            await bot.draw_minecraft_item_gacha(second)  # type: ignore[arg-type]

    asyncio.run(exercise())

    gives = [command for command in rcon.commands if command.startswith("give Steve ")]
    assert gives == ["give Steve minecraft:iron_ingot 24"]
    assert "再抽選は行いません" in first.followup.send.await_args.args[0]
    assert "再抽選は行いません" in second.followup.send.await_args.args[0]
    channel.send.assert_not_awaited()


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
