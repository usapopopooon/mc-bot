from __future__ import annotations

import discord

from mc_bot.deaths import translate_death
from mc_bot.events import EventType, LogEvent
from mc_bot.translations import AdvancementTranslator

_DISCORD_EMBED_DESCRIPTION_LIMIT = 4_096


def format_event(
    event: LogEvent,
    translator: AdvancementTranslator,
    discord_user_id: int | None = None,
) -> discord.Embed:
    player = _escape_markdown(event.player_name)
    add_honorific = event.type is not EventType.CHAT and not event.player_name.endswith("さん")
    if discord_user_id is not None:
        honorific = " さん" if add_honorific else ""
        identity = f"**{player} (<@{discord_user_id}>){honorific}**"
    else:
        if add_honorific:
            player = f"{player}さん"
        identity = f"**{player}**"

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
        case EventType.DEATH:
            death = _escape_markdown(translate_death(event.detail))
            message = f"💀 {identity} が{death}"
            color = discord.Color.dark_red()

    if len(message) > _DISCORD_EMBED_DESCRIPTION_LIMIT:
        message = f"{message[: _DISCORD_EMBED_DESCRIPTION_LIMIT - 1]}…"
    return discord.Embed(description=message, color=color)


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_~`|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
