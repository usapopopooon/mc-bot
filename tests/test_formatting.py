from mc_bot.events import EventType, LogEvent
from mc_bot.formatting import format_event
from mc_bot.translations import AdvancementTranslator


def test_formats_japanese_advancement() -> None:
    embed = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"),
        AdvancementTranslator.load(),
    )
    assert embed.description == "🏆 **Steve** が進捗「石器時代」を達成しました"
    assert embed.color.value == 0xF1C40F


def test_preserves_unknown_advancement() -> None:
    embed = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Custom Advancement"),
        AdvancementTranslator.load(),
    )
    assert embed.description == "🏆 **Steve** が進捗「Custom Advancement」を達成しました"


def test_escapes_player_markdown_and_limits_length() -> None:
    embed = format_event(
        LogEvent(EventType.CHAT, "*player*", "x" * 4_200),
        AdvancementTranslator.load(),
    )
    assert embed.description is not None
    assert embed.description.startswith("💬 **\\*player\\***: ")
    assert len(embed.description) == 4_096
    assert embed.description.endswith("…")


def test_includes_linked_discord_username_without_mention_syntax() -> None:
    embed = format_event(
        LogEvent(EventType.LEAVE, ".hoge"),
        AdvancementTranslator.load(),
        "hoge",
    )

    assert embed.description == "🔴 **.hoge** (@hoge) が退出しました"
