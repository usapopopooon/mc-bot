from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import discord

_SERVER_LIST = re.compile(
    r"\bThere are\s+(\d+)\s+of a max of\s+(\d+)\s+players online:\s*(.*)$",
    re.IGNORECASE,
)
_MAX_DISPLAYED_PLAYERS = 50
_EMBED_FIELD_LIMIT = 1_024


@dataclass(frozen=True, slots=True)
class StatusPlayer:
    minecraft_name: str
    discord_user_id: int | None


@dataclass(frozen=True, slots=True)
class ServerStatusSnapshot:
    online: bool
    players: tuple[StatusPlayer, ...]
    max_players: int | None
    checked_at: datetime


def parse_server_list_response(response: str) -> tuple[list[str], int]:
    match = _SERVER_LIST.search(response.strip())
    if match is None:
        raise ValueError("Minecraftのオンライン情報を読み取れませんでした")
    expected_count = int(match[1])
    max_players = int(match[2])
    names_text = match[3].strip()
    names = [name.strip() for name in names_text.split(",") if name.strip()]
    if len(names) != expected_count:
        raise ValueError("Minecraftのオンライン人数とプレイヤー一覧が一致しません")
    return names, max_players


def status_panel_embed(snapshot: ServerStatusSnapshot) -> discord.Embed:
    if not snapshot.online:
        embed = discord.Embed(
            title="🎮 Minecraftサーバーステータス",
            description="🔴 **オフライン**",
            color=discord.Color.red(),
            timestamp=snapshot.checked_at,
        )
        embed.add_field(name="オンライン", value="—", inline=False)
        embed.set_footer(text="最終確認")
        return embed

    count = len(snapshot.players)
    maximum = snapshot.max_players if snapshot.max_players is not None else "?"
    embed = discord.Embed(
        title="🎮 Minecraftサーバーステータス",
        description="🟢 **オンライン**",
        color=discord.Color.green(),
        timestamp=snapshot.checked_at,
    )
    embed.add_field(name="オンライン", value=f"**{count} / {maximum}人**", inline=False)
    if not snapshot.players:
        embed.add_field(
            name="参加中のプレイヤー",
            value="現在オンラインのプレイヤーはいません。",
            inline=False,
        )
    else:
        visible = snapshot.players[:_MAX_DISPLAYED_PLAYERS]
        lines = [_player_line(player) for player in visible]
        omitted = count - len(visible)
        if omitted > 0:
            lines.append(f"ほか {omitted}人")
        for index, chunk in enumerate(_chunk_lines(lines)):
            embed.add_field(
                name="参加中のプレイヤー" if index == 0 else "参加中のプレイヤー (続き)",
                value=chunk,
                inline=False,
            )
    embed.set_footer(text="最終更新")
    return embed


def _player_line(player: StatusPlayer) -> str:
    minecraft_name = _escape_markdown(player.minecraft_name)
    if player.discord_user_id is None:
        return f"• **{minecraft_name}** (Discord未連携)"
    return f"• **{minecraft_name}** (<@{player.discord_user_id}>)"


def _chunk_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in lines:
        added_length = len(line) + (1 if current else 0)
        if current and current_length + added_length > _EMBED_FIELD_LIMIT:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
            added_length = len(line)
        current.append(line)
        current_length += added_length
    if current:
        chunks.append("\n".join(current))
    return chunks


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_~`|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
