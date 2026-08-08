from __future__ import annotations

import discord

from mc_bot.deaths import translate_death
from mc_bot.events import EventType, LogEvent
from mc_bot.experience import (
    ADVANCEMENT_REWARD_IN_GAME_XP,
    ADVANCEMENT_REWARD_LEVEL_BOT_XP,
    MinecraftLevelUpEvent,
)
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
    server_reward_xp: int = ADVANCEMENT_REWARD_LEVEL_BOT_XP,
    minecraft_reward_xp: int | None = ADVANCEMENT_REWARD_IN_GAME_XP,
) -> discord.Embed:
    """既存の進捗ログに続けて送るlevel-bot XP報酬Embed。"""
    player = _escape_markdown(event.player_name)
    advancement = _escape_markdown(advancement)
    server_name = _escape_markdown(server_name)
    reward = f"**{server_reward_xp} XP**"
    if minecraft_reward_xp is not None:
        reward += f"とMinecraft内の **{minecraft_reward_xp} XP**"
    return discord.Embed(
        description=(
            f"✨ **[{server_name}] {player} (<@{discord_user_id}>) さん** が"
            f"進捗「{advancement}」を達成したので、サーバーでの "
            f"{reward}を獲得しました!"
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


def format_xp_exchange(
    *,
    server_name: str,
    player_name: str,
    discord_user_id: int,
    cost_xp: int,
    reward_xp: int,
) -> discord.Embed:
    """サーバーXPからMinecraft内XPへの交換成功ログ。"""
    server_name = _escape_markdown(server_name)
    player_name = _escape_markdown(player_name)
    return discord.Embed(
        description=(
            f"⛏️ **[{server_name}] {player_name} (<@{discord_user_id}>) さん** が"
            f"サーバーXP **{cost_xp:,}**を交換し、Minecraft内の "
            f"**{reward_xp:,} XP**を獲得しました!"
        ),
        color=discord.Color.green(),
    )


def format_resource_exchange(
    *,
    server_name: str,
    player_name: str,
    discord_user_id: int,
    cost_xp: int,
    item_name: str,
    item_count: int,
) -> discord.Embed:
    """サーバーXPからMinecraft内資源への交換成功ログ。"""
    server_name = _escape_markdown(server_name)
    player_name = _escape_markdown(player_name)
    item_name = _escape_markdown(item_name)
    return discord.Embed(
        description=(
            f"💎 **[{server_name}] {player_name} (<@{discord_user_id}>) さん** が"
            f"サーバーXP **{cost_xp:,}**を交換し、Minecraft内の "
            f"**{item_name} x{item_count:,}**を獲得しました!"
        ),
        color=discord.Color.green(),
    )


def format_fishing_combo_milestone(
    *, player_name: str, discord_user_id: int, combo_count: int, reward_xp: int
) -> discord.Embed:
    player_name = _escape_markdown(player_name)
    return discord.Embed(
        description=(
            f"🎣 **{player_name} (<@{discord_user_id}>) さん** が釣り"
            f"**{combo_count}コンボ**を達成! **+{reward_xp} XP**"
        ),
        color=discord.Color.blue(),
    )


def format_woodcutting_combo_milestone(
    *, player_name: str, discord_user_id: int, combo_count: int, reward_xp: int
) -> discord.Embed:
    player_name = _escape_markdown(player_name)
    return discord.Embed(
        description=(
            f"🪓 **{player_name} (<@{discord_user_id}>) さん** が連続伐採"
            f"**{combo_count}本**を達成! **+{reward_xp} XP**"
        ),
        color=discord.Color.green(),
    )


def format_server_announcement(message: str) -> discord.Embed:
    """管理者がMinecraft内へ送った告知をDiscordログ用Embedにする。"""
    normalized = " ".join(message.split()).strip()
    return discord.Embed(
        description=f"📢 **[サーバー告知]** {_escape_markdown(normalized)}",
        color=discord.Color.gold(),
    )


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_~`|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
