from mc_bot.events import EventType, LogEvent
from mc_bot.formatting import format_event
from mc_bot.translations import AdvancementTranslator


def test_formats_japanese_advancement() -> None:
    embed = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"),
        AdvancementTranslator.load(),
    )
    assert embed.description == "🏆 **Steveさん** が進捗「石器時代」を達成しました"
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

    assert embed.description == "🔴 **.hogeさん (<@123456789>)** が退出しました"


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
