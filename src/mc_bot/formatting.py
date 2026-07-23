from __future__ import annotations

from mc_bot.events import EventType, LogEvent
from mc_bot.translations import AdvancementTranslator

_DISCORD_CONTENT_LIMIT = 2_000


def format_event(event: LogEvent, translator: AdvancementTranslator) -> str:
    player = _escape_markdown(event.player_name)

    match event.type:
        case EventType.CHAT:
            message = f"💬 **{player}**: {event.detail}"
        case EventType.ADVANCEMENT:
            advancement = translator.translate(event.detail)
            message = f"🏆 **{player}** が進捗「{advancement}」を達成しました"
        case EventType.JOIN:
            message = f"🟢 **{player}** が参加しました"
        case EventType.LEAVE:
            message = f"🔴 **{player}** が退出しました"

    if len(message) <= _DISCORD_CONTENT_LIMIT:
        return message
    return f"{message[: _DISCORD_CONTENT_LIMIT - 1]}…"


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_~`|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
