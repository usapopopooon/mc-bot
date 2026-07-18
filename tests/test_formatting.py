from mc_bot.events import EventType, LogEvent
from mc_bot.formatting import format_event
from mc_bot.translations import AdvancementTranslator


def test_formats_japanese_advancement() -> None:
    message = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"),
        "Chill Cafe",
        AdvancementTranslator.load(),
    )
    assert message == "**[Chill Cafe]** 🏆 **Steve** が進捗「石器時代」を達成しました"


def test_preserves_unknown_advancement() -> None:
    message = format_event(
        LogEvent(EventType.ADVANCEMENT, "Steve", "Custom Advancement"),
        "",
        AdvancementTranslator.load(),
    )
    assert message == "🏆 **Steve** が進捗「Custom Advancement」を達成しました"


def test_escapes_player_markdown_and_limits_length() -> None:
    message = format_event(
        LogEvent(EventType.CHAT, "*player*", "x" * 2_100),
        "",
        AdvancementTranslator.load(),
    )
    assert message.startswith("💬 **\\*player\\***: ")
    assert len(message) == 2_000
    assert message.endswith("…")
