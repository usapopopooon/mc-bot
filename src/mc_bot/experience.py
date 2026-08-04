from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import aiohttp

from mc_bot.accounts import MinecraftXpOutboxEvent

ADVANCEMENT_REWARD_LEVEL_BOT_XP = 100
MINECRAFT_XP_PER_LEVEL_BOT_XP = 100
ADVANCEMENT_REWARD_MINECRAFT_XP = ADVANCEMENT_REWARD_LEVEL_BOT_XP * MINECRAFT_XP_PER_LEVEL_BOT_XP

LOGGER = logging.getLogger(__name__)
_QUERY_RESULT = re.compile(r"\bhas\s+(\d+)\s+experience\s+(levels?|points?)\b", re.I)
_SAFE_PLAYER_NAME = re.compile(r"\.?[A-Za-z0-9_]{1,32}")


@dataclass(frozen=True, slots=True)
class MinecraftLevelUpEvent:
    id: int
    guild_id: int
    guild_name: str
    user_id: int
    display_name: str
    level: int
    minecraft_delivered: bool
    discord_delivered: bool


@dataclass(frozen=True, slots=True)
class MinecraftVoiceHeartbeatResult:
    awarded_bonus_seconds: int
    bonus_active: bool


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


def level_up_tellraw_command(event: MinecraftLevelUpEvent) -> str:
    """Discordサーバー名と表示名を使った安全な色付きtellrawを作る。"""
    components = [
        {"text": "["},
        {"text": event.guild_name, "color": "aqua"},
        {"text": "] "},
        {"text": event.display_name, "color": "yellow"},
        {"text": "さんがレベル "},
        {"text": str(event.level), "color": "green", "bold": True},
        {"text": " になりました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def advancement_reward_tellraw_command(
    server_name: str,
    player_name: str,
    advancement: str,
    reward_xp: int = ADVANCEMENT_REWARD_LEVEL_BOT_XP,
) -> str:
    """進捗達成報酬をMinecraft内へ別メッセージとして流す。"""
    components = [
        {"text": "["},
        {"text": server_name, "color": "aqua"},
        {"text": "] "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんが進捗「"},
        {"text": advancement, "color": "gold"},
        {"text": "」を達成したので、サーバーでの "},
        {"text": f"{reward_xp} XP", "color": "green", "bold": True},
        {"text": "を獲得しました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def voice_bonus_started_tellraw_command(server_name: str, player_name: str) -> str:
    """MinecraftとVCの同時接続ボーナス開始をMinecraft内へ流す。"""
    components = [
        {"text": "["},
        {"text": server_name, "color": "aqua"},
        {"text": "] "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんがMinecraftとVCに同時接続したので、"},
        {"text": "VC XPが2倍", "color": "green", "bold": True},
        {"text": "になりました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


class LevelBotXpClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._url = f"{self._base_url}/api/v1/integrations/minecraft/xp-events"
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def send(self, event: MinecraftXpOutboxEvent) -> bool:
        if not self._token:
            return False
        session = self._require_session()
        payload = {
            "event_id": event.event_id,
            "guild_id": str(event.guild_id),
            "user_id": str(event.discord_user_id),
            "minecraft_account_id": f"mc-bot:{event.account_id}",
            "minecraft_xp": event.minecraft_xp,
            "observed_at": event.observed_at,
        }
        try:
            async with session.post(
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

    async def send_voice_heartbeat(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        account_id: int,
        observed_at: str,
    ) -> MinecraftVoiceHeartbeatResult | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/voice-heartbeats",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "guild_id": str(guild_id),
                    "user_id": str(discord_user_id),
                    "minecraft_account_id": f"mc-bot:{account_id}",
                    "observed_at": observed_at,
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot voice heartbeat rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                payload = await response.json()
            awarded = payload["awarded_bonus_seconds"]
            active = payload["bonus_active"]
            if not isinstance(awarded, int) or awarded < 0 or not isinstance(active, bool):
                raise ValueError("voice heartbeat response contains invalid values")
            return MinecraftVoiceHeartbeatResult(
                awarded_bonus_seconds=awarded,
                bonus_active=active,
            )
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not send voice heartbeat to level-bot: %s", error)
            return None

    async def fetch_level_ups(self, guild_id: int) -> list[MinecraftLevelUpEvent] | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.get(
                f"{self._base_url}/api/v1/integrations/minecraft/level-up-events",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"guild_id": str(guild_id), "limit": "20"},
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot level-up API rejected fetch status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                payload = await response.json()
            if not isinstance(payload, list):
                raise ValueError("level-up response must be a list")
            return [self._parse_level_up_event(item) for item in payload]
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            ValueError,
            TypeError,
        ) as error:
            LOGGER.warning("Could not fetch level-ups from level-bot: %s", error)
            return None

    async def acknowledge_level_up(self, event_id: int, guild_id: int, destination: str) -> bool:
        if destination not in {"minecraft", "discord"}:
            raise ValueError("unknown level-up destination")
        if not self._token:
            return False
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/level-up-events/{event_id}/ack",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"guild_id": str(guild_id), "destination": destination},
            ) as response:
                if response.status == 204:
                    return True
                body = await response.text()
                LOGGER.warning(
                    "level-bot level-up API rejected ack status=%d body=%s",
                    response.status,
                    body[:300],
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            LOGGER.warning("Could not acknowledge level-up to level-bot: %s", error)
        return False

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    @staticmethod
    def _parse_level_up_event(item: object) -> MinecraftLevelUpEvent:
        if not isinstance(item, dict):
            raise ValueError("level-up event must be an object")
        event = MinecraftLevelUpEvent(
            id=int(item["id"]),
            guild_id=int(item["guild_id"]),
            guild_name=str(item["guild_name"]),
            user_id=int(item["user_id"]),
            display_name=str(item["display_name"]),
            level=int(item["level"]),
            minecraft_delivered=item["minecraft_delivered"],
            discord_delivered=item["discord_delivered"],
        )
        if (
            event.id <= 0
            or event.guild_id <= 0
            or event.user_id <= 0
            or event.level <= 0
            or not event.guild_name
            or not event.display_name
            or not isinstance(event.minecraft_delivered, bool)
            or not isinstance(event.discord_delivered, bool)
        ):
            raise ValueError("level-up event contains invalid values")
        return event
