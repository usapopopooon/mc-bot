from __future__ import annotations

import discord

from mc_bot.events import EventType, LogEvent
from mc_bot.translations import AdvancementTranslator

_DISCORD_EMBED_DESCRIPTION_LIMIT = 4_096


def format_event(
    event: LogEvent,
    translator: AdvancementTranslator,
    discord_username: str | None = None,
) -> discord.Embed:
    player = _escape_markdown(event.player_name)
    identity = f"**{player}**"
    if discord_username:
        identity += f" (@{_escape_markdown(discord_username)})"

    match event.type:
        case EventType.CHAT:
            message = f"💬 {identity}: {event.detail}"
            color = discord.Color.blue()
        case EventType.ADVANCEMENT:
            advancement = translator.translate(event.detail)
            message = f"🏆 {identity} が進捗「{advancement}」を達成しました"
            color = discord.Color.gold()
        case EventType.JOIN:
            message = f"🟢 {identity} が参加しました"
            color = discord.Color.green()
        case EventType.LEAVE:
            message = f"🔴 {identity} が退出しました"
            color = discord.Color.red()

    if len(message) > _DISCORD_EMBED_DESCRIPTION_LIMIT:
        message = f"{message[: _DISCORD_EMBED_DESCRIPTION_LIMIT - 1]}…"
    return discord.Embed(description=message, color=color)


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_~`|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
