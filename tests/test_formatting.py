from mc_bot.events import EventType, LogEvent
from mc_bot.experience import MinecraftLevelUpEvent
from mc_bot.formatting import (
    format_advancement_reward,
    format_diamond_emerald_exchange,
    format_emerald_diamond_exchange,
    format_event,
    format_fishing_combo_milestone,
    format_level_up_event,
    format_market_cancellation,
    format_market_purchase,
    format_resource_exchange,
    format_server_announcement,
    format_server_xp_started,
    format_voice_bonus_started,
    format_woodcutting_combo_milestone,
    format_xp_exchange,
)
from mc_bot.translations import AdvancementTranslator


def test_formats_emerald_diamond_exchange_as_non_notifying_user_reference() -> None:
    embed = format_emerald_diamond_exchange(
        server_name="うさぽサーバー",
        player_name="Steve",
        discord_user_id=123,
        emerald_count=32,
        diamond_count=2,
    )

    assert "Steve (<@123>)" in str(embed.description)
    assert "エメラルド x32" in str(embed.description)
    assert "ダイヤモンド x2" in str(embed.description)


def test_formats_diamond_emerald_exchange_as_non_notifying_user_reference() -> None:
    embed = format_diamond_emerald_exchange(
        server_name="うさぽサーバー",
        player_name="Steve",
        discord_user_id=123,
        diamond_count=1,
        emerald_count=16,
    )

    assert "Steve (<@123>)" in str(embed.description)
    assert "ダイヤモンド x1" in str(embed.description)
    assert "エメラルド x16" in str(embed.description)


def test_formats_japanese_advancement() -> None:
    embed = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"),
        AdvancementTranslator.load(),
    )
    assert embed.description == "🏆 **Steveさん** が進捗「石器時代」を達成しました"
    assert embed.color.value == 0xF1C40F


def test_formats_advancement_reward_as_a_separate_embed() -> None:
    embed = format_advancement_reward(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"),
        "石器時代",
        "うさぽサーバー",
        123,
    )

    assert embed.description == (
        "✨ **[うさぽサーバー] Steve (<@123>) さん** が進捗「石器時代」を"
        "達成したので、サーバーでの **100 XP**とMinecraft内の "
        "**100 XP**を獲得しました!"
    )
    assert embed.color.value == 0x2ECC71


def test_advancement_reward_omits_minecraft_xp_without_rcon() -> None:
    embed = format_advancement_reward(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"),
        "石器時代",
        "うさぽサーバー",
        123,
        minecraft_reward_xp=None,
    )

    assert embed.description == (
        "✨ **[うさぽサーバー] Steve (<@123>) さん** が進捗「石器時代」を"
        "達成したので、サーバーでの **100 XP**を獲得しました!"
    )


def test_formats_voice_bonus_started_without_ping() -> None:
    embed = format_voice_bonus_started(
        server_name="うさぽサーバー",
        player_name="Steve",
        discord_user_id=123,
    )

    assert embed.description == (
        "🎮🔊 **[うさぽサーバー] Steve (<@123>) さん** が"
        "MinecraftとVCに同時接続したので、"
        "**VC XPとMinecraft内の経験値が2倍**になりました!"
    )
    assert embed.color.value == 0x2ECC71


def test_formats_server_xp_started() -> None:
    embed = format_server_xp_started(
        server_name="うさぽサーバー",
        player_name="Steve",
        discord_user_id=123,
    )

    assert embed.description == (
        "🎮 **[うさぽサーバー] Steve (<@123>) さん** は"
        "マイクラで遊んでいる間、**サーバーXP**を獲得します!"
    )
    assert embed.color.value == 0x2ECC71


def test_formats_minecraft_xp_exchange() -> None:
    embed = format_xp_exchange(
        server_name="うさぽサーバー",
        player_name="Steve",
        discord_user_id=123,
        cost_xp=10,
        reward_xp=100,
    )

    assert embed.description == (
        "⛏️ **[うさぽサーバー] Steve (<@123>) さん** が"
        "サーバーXP **10**を交換し、Minecraft内の **100 XP**を獲得しました!"
    )
    assert embed.color.value == 0x2ECC71


def test_formats_minecraft_resource_exchange() -> None:
    embed = format_resource_exchange(
        server_name="うさぽサーバー",
        player_name="*Steve*",
        discord_user_id=123,
        cost_xp=550,
        item_name="ダイヤモンド",
        item_count=3,
    )

    assert embed.description == (
        r"💎 **[うさぽサーバー] \*Steve\* (<@123>) さん** が"
        "サーバーXP **550**を交換し、Minecraft内の "
        "**ダイヤモンド x3**を獲得しました!"
    )
    assert embed.color.value == 0x2ECC71


def test_formats_market_purchase_with_buyer_seller_item_and_price() -> None:
    embed = format_market_purchase(
        server_name="うさぽサーバー",
        buyer_name="*Buyer*",
        buyer_discord_user_id=123,
        seller_name="_Seller_",
        seller_discord_user_id=456,
        item_name="古代の*残骸*",
        item_count=2,
        price_xp=3_000,
    )

    assert embed.description == (
        r"🛒 **[うさぽサーバー] \*Buyer\* (<@123>) さん** が"
        r"**\_Seller\_ (<@456>) さん** から"
        r"**古代の\*残骸\* x2**を **3,000 サーバーXP**で購入しました!"
    )
    assert embed.color.value == 0x3498DB


def test_formats_market_cancellation_with_seller_item_price_and_record_id() -> None:
    embed = format_market_cancellation(
        server_name="うさぽサーバー",
        listing_id=17,
        seller_name="_Seller_",
        seller_discord_user_id=456,
        item_name="古代の*残骸*",
        item_count=2,
        price_xp=3_000,
        record_id="44444444-4444-4444-8444-444444444444",
    )

    assert embed.title == "🗑️ [うさぽサーバー] フリマ #17 出品取消"
    assert embed.description == (
        r"**\_Seller\_ (<@456>) さん** が"
        r"**古代の\*残骸\* x2** / **3,000 サーバーXP** の出品を取り消しました。"
    )
    assert embed.footer.text == "記録ID: 44444444-4444-4444-8444-444444444444"
    assert embed.color.value == 0x607D8B


def test_formats_public_combo_milestones_for_discord_log() -> None:
    fishing = format_fishing_combo_milestone(
        player_name="*Steve*", discord_user_id=123, combo_count=10, reward_xp=15
    )
    woodcutting = format_woodcutting_combo_milestone(
        player_name="*Steve*", discord_user_id=123, combo_count=20, reward_xp=30
    )
    assert fishing.description == (
        r"🎣 **\*Steve\* (<@123>) さん** が釣り**10コンボ**を達成! **+15 XP**"
    )
    assert woodcutting.description == (
        r"🪓 **\*Steve\* (<@123>) さん** が連続伐採**20本**を達成! **+30 XP**"
    )


def test_formats_server_announcement_like_minecraft_message() -> None:
    embed = format_server_announcement("  メンテナンスを **開始** します  ")

    assert embed.description == "📢 **[サーバー告知]** メンテナンスを \\*\\*開始\\*\\* します"
    assert embed.color.value == 0xF1C40F


def test_formats_level_bot_level_up_as_linked_log_without_title() -> None:
    embed = format_level_up_event(
        MinecraftLevelUpEvent(
            id=1,
            guild_id=456,
            guild_name="うさぽサーバー",
            user_id=123,
            display_name="*うさぽ*",
            level=10,
            minecraft_delivered=True,
            discord_delivered=False,
        )
    )

    assert embed.title is None
    assert embed.description == (
        r"🎉 **[うさぽサーバー] \*うさぽ\* (<@123>) さん** が"
        "レベル **10** になりました!"
    )
    assert embed.color.value == 0xF1C40F


def test_preserves_unknown_advancement() -> None:
    embed = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Custom Advancement"),
        AdvancementTranslator.load(),
    )
    assert embed.description == "🏆 **Steveさん** が進捗「Custom Advancement」を達成しました"


def test_escapes_player_markdown_and_limits_length() -> None:
    embed = format_event(
        LogEvent(EventType.CHAT, "*player*", "x" * 4_200),
        AdvancementTranslator.load(),
    )
    assert embed.description is not None
    assert embed.description.startswith("💬 **\\*player\\***: ")
    assert len(embed.description) == 4_096
    assert embed.description.endswith("…")


def test_includes_linked_discord_user_as_clickable_mention() -> None:
    embed = format_event(
        LogEvent(EventType.LEAVE, ".hoge"),
        AdvancementTranslator.load(),
        123456789,
    )

    assert embed.description == "🔴 **.hoge (<@123456789>) さん** が退出しました"


def test_places_honorific_after_linked_identity_for_advancement() -> None:
    embed = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"),
        AdvancementTranslator.load(),
        123456789,
    )

    assert embed.description == (
        "🏆 **Steve (<@123456789>) さん** が進捗「石器時代」を達成しました"
    )


def test_adds_honorific_to_join_log_without_discord_link() -> None:
    embed = format_event(
        LogEvent(EventType.JOIN, "Steve"),
        AdvancementTranslator.load(),
    )

    assert embed.description == "🟢 **Steveさん** が参加しました"


def test_does_not_duplicate_honorific_in_join_or_leave_log() -> None:
    embed = format_event(
        LogEvent(EventType.LEAVE, "さよさん"),
        AdvancementTranslator.load(),
    )

    assert embed.description == "🔴 **さよさん** が退出しました"


def test_formats_death_with_linked_identity_without_notification_ping() -> None:
    embed = format_event(
        LogEvent(EventType.DEATH, "Steve", "was slain by Zombie"),
        AdvancementTranslator.load(),
        123456789,
    )

    assert embed.description == "💀 **Steve (<@123456789>) さん** はゾンビに殺害された"
    assert embed.color.value == 0x992D22


def test_formats_environmental_death_in_japanese() -> None:
    embed = format_event(
        LogEvent(EventType.DEATH, ".Bedrock_Player", "fell from a high place"),
        AdvancementTranslator.load(),
    )

    assert embed.description == r"💀 **.Bedrock\_Playerさん** は高い所から落ちた"


def test_escapes_attacker_markdown_in_death_log() -> None:
    embed = format_event(
        LogEvent(EventType.DEATH, "Steve", "was slain by Bad_Name"),
        AdvancementTranslator.load(),
    )

    assert embed.description == r"💀 **Steveさん** はBad\_Nameに殺害された"


def test_translates_vanilla_mob_attacker_names() -> None:
    embed = format_event(
        LogEvent(EventType.DEATH, "Steve", "was shot by Skeleton"),
        AdvancementTranslator.load(),
    )

    assert embed.description == "💀 **Steveさん** はスケルトンに射抜かれた"
