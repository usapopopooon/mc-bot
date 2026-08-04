from __future__ import annotations

import discord

from mc_bot.deaths import translate_death
from mc_bot.events import EventType, LogEvent
from mc_bot.experience import ADVANCEMENT_REWARD_LEVEL_BOT_XP, MinecraftLevelUpEvent
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
            message = f"💀 {identity} は{death}"
            color = discord.Color.dark_red()

    if len(message) > _DISCORD_EMBED_DESCRIPTION_LIMIT:
        message = f"{message[: _DISCORD_EMBED_DESCRIPTION_LIMIT - 1]}…"
    return discord.Embed(description=message, color=color)


def format_level_up_event(event: MinecraftLevelUpEvent) -> discord.Embed:
    """Minecraft内へ転送したlevel-botのレベルアップをログ用Embedにする。"""
    guild_name = _escape_markdown(event.guild_name)
    display_name = _escape_markdown(event.display_name)
    return discord.Embed(
        description=(
            f"🎉 **[{guild_name}] {display_name} (<@{event.user_id}>) さん** が"
            f"レベル **{event.level}** になりました!"
        ),
        color=discord.Color.gold(),
    )


def format_advancement_reward(
    event: LogEvent,
    advancement: str,
    server_name: str,
    discord_user_id: int,
    reward_xp: int = ADVANCEMENT_REWARD_LEVEL_BOT_XP,
) -> discord.Embed:
    """既存の進捗ログに続けて送るlevel-bot XP報酬Embed。"""
    player = _escape_markdown(event.player_name)
    advancement = _escape_markdown(advancement)
    server_name = _escape_markdown(server_name)
    return discord.Embed(
        description=(
            f"✨ **[{server_name}] {player} (<@{discord_user_id}>) さん** が"
            f"進捗「{advancement}」を達成したので、サーバーでの "
            f"**{reward_xp} XP**を獲得しました!"
        ),
        color=discord.Color.green(),
    )


def format_voice_bonus_started(
    *,
    server_name: str,
    player_name: str,
    discord_user_id: int,
) -> discord.Embed:
    """MinecraftとVCの同時接続ボーナス開始ログ。"""
    server_name = _escape_markdown(server_name)
    player_name = _escape_markdown(player_name)
    return discord.Embed(
        description=(
            f"🎮🔊 **[{server_name}] {player_name} (<@{discord_user_id}>) さん** が"
            "MinecraftとVCに同時接続したので、"
            "**VC XPとMinecraft内の経験値が2倍**になりました!"
        ),
        color=discord.Color.green(),
    )


def format_server_xp_started(
    *,
    server_name: str,
    player_name: str,
    discord_user_id: int,
) -> discord.Embed:
    """Minecraft参加中にサーバーXPを獲得することを知らせるログ。"""
    server_name = _escape_markdown(server_name)
    player_name = _escape_markdown(player_name)
    return discord.Embed(
        description=(
            f"🎮 **[{server_name}] {player_name} (<@{discord_user_id}>) さん** は"
            "マイクラで遊んでいる間、**サーバーXP**を獲得します!"
        ),
        color=discord.Color.green(),
    )


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_~`|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
