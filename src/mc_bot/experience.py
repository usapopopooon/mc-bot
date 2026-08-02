from __future__ import annotations

import logging
import re

import aiohttp

from mc_bot.accounts import MinecraftXpOutboxEvent

LOGGER = logging.getLogger(__name__)
_QUERY_RESULT = re.compile(r"\bhas\s+(\d+)\s+experience\s+(levels?|points?)\b", re.I)
_SAFE_PLAYER_NAME = re.compile(r"\.?[A-Za-z0-9_]{1,32}")


def parse_experience_query(response: str, unit: str) -> int:
    """``experience query`` の英語RCON応答から値を取り出す。"""
    match = _QUERY_RESULT.search(response)
    expected = unit.removesuffix("s")
    if match is None or match[2].lower().removesuffix("s") != expected:
        raise ValueError(f"Minecraft経験値の{unit}応答を読み取れませんでした")
    return int(match[1])


def experience_to_next_level(level: int) -> int:
    if level < 0:
        raise ValueError("level must not be negative")
    if level <= 15:
        return 2 * level + 7
    if level <= 30:
        return 5 * level - 38
    return 9 * level - 158


def total_experience_points(level: int, points_into_level: int) -> int:
    """表示レベルとレベル内ポイントを現在の総XPポイントへ変換する。"""
    if level < 0 or not 0 <= points_into_level < experience_to_next_level(level):
        raise ValueError("Minecraft経験値のレベルとポイントが一致しません")
    if level <= 16:
        level_floor = level * level + 6 * level
    elif level <= 31:
        level_floor = (5 * level * level - 81 * level + 720) // 2
    else:
        level_floor = (9 * level * level - 325 * level + 4440) // 2
    return level_floor + points_into_level


def experience_query_command(player_name: str, unit: str) -> str:
    if unit not in {"levels", "points"}:
        raise ValueError("unit must be levels or points")
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
    return f"experience query {player_name} {unit}"


class LevelBotXpClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._url = f"{base_url.rstrip('/')}/api/v1/integrations/minecraft/xp-events"
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def send(self, event: MinecraftXpOutboxEvent) -> bool:
        if not self._token:
            return False
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        payload = {
            "event_id": event.event_id,
            "guild_id": str(event.guild_id),
            "user_id": str(event.discord_user_id),
            "minecraft_account_id": f"mc-bot:{event.account_id}",
            "minecraft_xp": event.minecraft_xp,
            "observed_at": event.observed_at,
        }
        try:
            async with self._session.post(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload,
            ) as response:
                if response.status == 200:
                    return True
                body = await response.text()
                LOGGER.warning(
                    "level-bot XP API rejected event status=%d body=%s",
                    response.status,
                    body[:300],
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            LOGGER.warning("Could not send Minecraft XP to level-bot: %s", error)
        return False
