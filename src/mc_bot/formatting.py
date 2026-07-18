from __future__ import annotations

from mc_bot.events import EventType, LogEvent
from mc_bot.translations import AdvancementTranslator

_DISCORD_CONTENT_LIMIT = 2_000


def format_event(event: LogEvent, server_label: str, translator: AdvancementTranslator) -> str:
    prefix = f"**[{_escape_markdown(server_label)}]** " if server_label else ""
    player = _escape_markdown(event.player_name)

    match event.type:
        case EventType.CHAT:
            message = f"{prefix}💬 **{player}**: {event.detail}"
        case EventType.ADVANCEMENT:
            advancement = translator.translate(event.detail)
            message = f"{prefix}🏆 **{player}** が進捗「{advancement}」を達成しました"
        case EventType.JOIN:
            message = f"{prefix}🟢 **{player}** が参加しました"
        case EventType.LEAVE:
            message = f"{prefix}🔴 **{player}** が退出しました"

    if len(message) <= _DISCORD_CONTENT_LIMIT:
        return message
    return f"{message[: _DISCORD_CONTENT_LIMIT - 1]}…"


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_~`|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
