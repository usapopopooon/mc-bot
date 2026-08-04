from mc_bot.events import EventType, LogEvent
from mc_bot.experience import MinecraftLevelUpEvent
from mc_bot.formatting import (
    format_advancement_reward,
    format_event,
    format_level_up_event,
)
from mc_bot.translations import AdvancementTranslator


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
        "達成したので、サーバーでの **100 XP**を獲得しました!"
    )
    assert embed.color.value == 0x2ECC71


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
        r"🎉 **\*うさぽ\* (<@123>) さん** がlevel-botでレベル **10** になりました!"
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
