from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass

import aiohttp

from mc_bot.accounts import (
    FishingComboRewardEvent,
    MinecraftXpOutboxEvent,
    WoodcuttingComboRewardEvent,
)

ADVANCEMENT_REWARD_LEVEL_BOT_XP = 100
MINECRAFT_XP_PER_LEVEL_BOT_XP = 100
ADVANCEMENT_REWARD_LEVEL_BOT_SOURCE_XP = (
    ADVANCEMENT_REWARD_LEVEL_BOT_XP * MINECRAFT_XP_PER_LEVEL_BOT_XP
)
ADVANCEMENT_REWARD_IN_GAME_XP = 100

LOGGER = logging.getLogger(__name__)
_QUERY_RESULT = re.compile(r"\bhas\s+(\d+)\s+experience\s+(levels?|points?)\b", re.I)
_SAFE_PLAYER_NAME = re.compile(r"\.?[A-Za-z0-9_]{1,32}")
_RESOURCE_ITEM_NAMES = {
    "minecraft:diamond": "ダイヤモンド",
    "minecraft:emerald": "エメラルド",
}


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


@dataclass(frozen=True, slots=True)
class MinecraftXpExchangeEvent:
    id: int
    event_id: str
    guild_id: int
    user_id: int
    minecraft_account_id: str
    cost_xp: int
    reward_xp: int
    status: str


@dataclass(frozen=True, slots=True)
class MinecraftXpWallet:
    total_xp: int
    spent_xp: int
    available_xp: int


@dataclass(frozen=True, slots=True)
class MinecraftXpPack:
    cost_xp: int
    reward_xp: int


@dataclass(frozen=True, slots=True)
class MinecraftXpShop:
    wallet: MinecraftXpWallet
    packs: tuple[MinecraftXpPack, ...]


@dataclass(frozen=True, slots=True)
class MinecraftXpExchangeRequest:
    status: str
    message: str
    wallet_before: MinecraftXpWallet
    wallet_after: MinecraftXpWallet
    pack: MinecraftXpPack | None


@dataclass(frozen=True, slots=True)
class MinecraftResourcePack:
    item_id: str
    item_name: str
    item_count: int
    cost_xp: int


@dataclass(frozen=True, slots=True)
class MinecraftResourceShop:
    wallet: MinecraftXpWallet
    packs: tuple[MinecraftResourcePack, ...]


@dataclass(frozen=True, slots=True)
class MinecraftResourceExchangeRequest:
    status: str
    message: str
    wallet_before: MinecraftXpWallet
    wallet_after: MinecraftXpWallet
    pack: MinecraftResourcePack | None


@dataclass(frozen=True, slots=True)
class MinecraftResourceExchangeEvent:
    id: int
    event_id: str
    guild_id: int
    user_id: int
    minecraft_account_id: str
    item_id: str
    item_name: str
    item_count: int
    cost_xp: int
    status: str


@dataclass(frozen=True, slots=True)
class MinecraftItemGachaOffer:
    cost_xp: int
    normal_cost_xp: int
    premium_cost_xp: int
    daily_limit: int
    wallet: MinecraftXpWallet


@dataclass(frozen=True, slots=True)
class MinecraftItemGachaSpendRequest:
    status: str
    message: str
    cost_xp: int
    wallet_before: MinecraftXpWallet
    wallet_after: MinecraftXpWallet


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


def experience_add_points_command(player_name: str, points: int) -> str:
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
    if points <= 0:
        raise ValueError("points must be positive")
    return f"experience add {player_name} {points} points"


def voice_bonus_state_command(player_uuid: str, *, active: bool) -> str:
    normalized_uuid = str(uuid.UUID(player_uuid))
    state = "on" if active else "off"
    return f"usapo-event-bridge voice-bonus {normalized_uuid} {state}"


def actionbar_clear_command(player_name: str) -> str:
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
    return f'title {player_name} actionbar {{"text":""}}'


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
    server_reward_xp: int = ADVANCEMENT_REWARD_LEVEL_BOT_XP,
    minecraft_reward_xp: int = ADVANCEMENT_REWARD_IN_GAME_XP,
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
        {"text": f"{server_reward_xp} XP", "color": "green", "bold": True},
        {"text": "とMinecraft内の "},
        {"text": f"{minecraft_reward_xp} XP", "color": "green", "bold": True},
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
        {
            "text": "VC XPとMinecraft内の経験値が2倍",
            "color": "green",
            "bold": True,
        },
        {"text": "になりました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def server_xp_started_tellraw_command(server_name: str, player_name: str) -> str:
    """Minecraft参加中にサーバーXPを獲得することをMinecraft内へ流す。"""
    components = [
        {"text": "["},
        {"text": server_name, "color": "aqua"},
        {"text": "] "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんはマイクラで遊んでいる間、"},
        {"text": "サーバーXP", "color": "green", "bold": True},
        {"text": "を獲得します!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def xp_exchange_tellraw_command(
    server_name: str, player_name: str, cost_xp: int, reward_xp: int
) -> str:
    """サーバーXPからMinecraft内XPへの交換成功をゲーム内へ流す。"""
    components = [
        {"text": "["},
        {"text": server_name, "color": "aqua"},
        {"text": "] "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんがサーバーXP "},
        {"text": str(cost_xp), "color": "green", "bold": True},
        {"text": "を交換し、Minecraft内の "},
        {"text": f"{reward_xp} XP", "color": "green", "bold": True},
        {"text": "を獲得しました!"},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


class LevelBotXpClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._url = f"{self._base_url}/api/v1/integrations/minecraft/xp-events"
        self._fishing_url = f"{self._base_url}/api/v1/integrations/minecraft/fishing-combo-events"
        self._woodcutting_url = (
            f"{self._base_url}/api/v1/integrations/minecraft/woodcutting-combo-events"
        )
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

    async def send_fishing_combo(self, event: FishingComboRewardEvent) -> bool:
        """Send a Minecraft-only fishing reward for idempotent audit storage."""
        if not self._token:
            return False
        session = self._require_session()
        payload = {
            "event_id": event.event_id,
            "guild_id": str(event.guild_id),
            "user_id": str(event.discord_user_id),
            "minecraft_account_id": f"mc-bot:{event.account_id}",
            "catch_count": event.catch_count,
            "combo_count": event.combo_count,
            "reward_xp": event.reward_xp,
            "observed_at": event.observed_at,
        }
        try:
            async with session.post(
                self._fishing_url,
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload,
            ) as response:
                if response.status == 200:
                    return True
                body = await response.text()
                LOGGER.warning(
                    "level-bot fishing audit API rejected event status=%d body=%s",
                    response.status,
                    body[:300],
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            LOGGER.warning("Could not send fishing audit to level-bot: %s", error)
        return False

    async def send_woodcutting_combo(self, event: WoodcuttingComboRewardEvent) -> bool:
        """Send a Minecraft-only woodcutting reward for idempotent audit storage."""
        if not self._token:
            return False
        payload = {
            "event_id": event.event_id,
            "guild_id": str(event.guild_id),
            "user_id": str(event.discord_user_id),
            "minecraft_account_id": f"mc-bot:{event.account_id}",
            "log_count": event.log_count,
            "combo_count": event.combo_count,
            "reward_xp": event.reward_xp,
            "observed_at": event.observed_at,
        }
        try:
            async with self._require_session().post(
                self._woodcutting_url,
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload,
            ) as response:
                if response.status == 200:
                    return True
                body = await response.text()
                LOGGER.warning(
                    "level-bot woodcutting audit API rejected event status=%d body=%s",
                    response.status,
                    body[:300],
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            LOGGER.warning("Could not send woodcutting audit to level-bot: %s", error)
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

    async def fetch_xp_exchanges(self, guild_id: int) -> list[MinecraftXpExchangeEvent] | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.get(
                f"{self._base_url}/api/v1/integrations/minecraft/xp-exchanges",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"guild_id": str(guild_id), "limit": "20"},
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot XP exchange fetch rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                payload = await response.json()
            if not isinstance(payload, list):
                raise ValueError("XP exchange response must be a list")
            return [self._parse_xp_exchange(item) for item in payload]
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not fetch XP exchanges from level-bot: %s", error)
            return None

    async def fetch_xp_shop(self, guild_id: int, user_id: int) -> MinecraftXpShop | None:
        """交換レートとユーザーの共有XP残高をlevel-botから取得する。"""
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.get(
                f"{self._base_url}/api/v1/integrations/minecraft/xp-shop",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"guild_id": str(guild_id), "user_id": str(user_id)},
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot XP shop fetch rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                return self._parse_xp_shop(await response.json())
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not fetch XP shop from level-bot: %s", error)
            return None

    async def fetch_resource_shop(
        self, guild_id: int, user_id: int
    ) -> MinecraftResourceShop | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.get(
                f"{self._base_url}/api/v1/integrations/minecraft/resource-shop",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"guild_id": str(guild_id), "user_id": str(user_id)},
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot resource shop fetch rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                return self._parse_resource_shop(await response.json())
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not fetch resource shop from level-bot: %s", error)
            return None

    async def fetch_item_gacha_offer(
        self, guild_id: int, user_id: int
    ) -> MinecraftItemGachaOffer | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.get(
                f"{self._base_url}/api/v1/integrations/minecraft/item-gacha",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"guild_id": str(guild_id), "user_id": str(user_id)},
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot item gacha fetch rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                return self._parse_item_gacha_offer(await response.json())
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not fetch item gacha from level-bot: %s", error)
            return None

    async def request_xp_exchange(
        self,
        guild_id: int,
        user_id: int,
        request_id: str,
        cost_xp: int,
        expected_reward_xp: int,
    ) -> MinecraftXpExchangeRequest | None:
        """ユーザー操作による交換をlevel-botへ予約する。"""
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/xp-shop/exchanges",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "request_id": request_id,
                    "guild_id": str(guild_id),
                    "user_id": str(user_id),
                    "cost_xp": cost_xp,
                    "expected_reward_xp": expected_reward_xp,
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot XP shop exchange rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                return self._parse_xp_exchange_request(await response.json())
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not request XP exchange from level-bot: %s", error)
            return None

    async def request_resource_exchange(
        self,
        guild_id: int,
        user_id: int,
        request_id: str,
        item_id: str,
        item_count: int,
        expected_cost_xp: int,
    ) -> MinecraftResourceExchangeRequest | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/resource-shop/exchanges",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "request_id": request_id,
                    "guild_id": str(guild_id),
                    "user_id": str(user_id),
                    "item_id": item_id,
                    "item_count": item_count,
                    "expected_cost_xp": expected_cost_xp,
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot resource exchange rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                return self._parse_resource_exchange_request(await response.json())
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not request resource exchange from level-bot: %s", error)
            return None

    async def request_item_gacha_spend(
        self,
        *,
        guild_id: int,
        user_id: int,
        request_id: str,
        account_id: int,
        draw_day: str,
        expected_cost_xp: int,
    ) -> MinecraftItemGachaSpendRequest | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/item-gacha/spends",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "request_id": request_id,
                    "guild_id": str(guild_id),
                    "user_id": str(user_id),
                    "minecraft_account_id": f"mc-bot:{account_id}",
                    "draw_day": draw_day,
                    "expected_cost_xp": expected_cost_xp,
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot item gacha spend rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                return self._parse_item_gacha_spend_request(await response.json())
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not request item gacha spend from level-bot: %s", error)
            return None

    async def update_xp_exchange(
        self,
        event_id: int,
        guild_id: int,
        action: str,
        *,
        claim_token: str | None = None,
    ) -> bool:
        if action not in {"claim", "complete", "cancel"} or not self._token:
            return False
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/xp-exchanges/{event_id}/{action}",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"guild_id": str(guild_id), "claim_token": claim_token},
            ) as response:
                if response.status == 204:
                    return True
                body = await response.text()
                LOGGER.warning(
                    "level-bot XP exchange %s rejected status=%d body=%s",
                    action,
                    response.status,
                    body[:300],
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            LOGGER.warning("Could not %s XP exchange: %s", action, error)
        return False

    async def fetch_resource_exchanges(
        self, guild_id: int
    ) -> list[MinecraftResourceExchangeEvent] | None:
        if not self._token:
            return None
        session = self._require_session()
        try:
            async with session.get(
                f"{self._base_url}/api/v1/integrations/minecraft/resource-exchanges",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"guild_id": str(guild_id), "limit": "20"},
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "level-bot resource exchanges fetch rejected status=%d body=%s",
                        response.status,
                        body[:300],
                    )
                    return None
                payload = await response.json()
            if not isinstance(payload, list):
                raise ValueError("resource exchange response must be a list")
            return [self._parse_resource_exchange(item) for item in payload]
        except (
            aiohttp.ClientError,
            TimeoutError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            LOGGER.warning("Could not fetch resource exchanges from level-bot: %s", error)
            return None

    async def update_resource_exchange(
        self,
        event_id: int,
        guild_id: int,
        action: str,
        *,
        claim_token: str | None = None,
    ) -> bool:
        if action not in {"claim", "complete", "cancel"} or not self._token:
            return False
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/"
                f"resource-exchanges/{event_id}/{action}",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"guild_id": str(guild_id), "claim_token": claim_token},
            ) as response:
                if response.status == 204:
                    return True
                body = await response.text()
                LOGGER.warning(
                    "level-bot resource exchange %s rejected status=%d body=%s",
                    action,
                    response.status,
                    body[:300],
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            LOGGER.warning("Could not %s resource exchange: %s", action, error)
        return False

    async def update_item_gacha_spend(
        self,
        *,
        request_id: str,
        guild_id: int,
        user_id: int,
        action: str,
    ) -> bool:
        if action not in {"complete", "cancel"} or not self._token:
            return False
        session = self._require_session()
        try:
            async with session.post(
                f"{self._base_url}/api/v1/integrations/minecraft/"
                f"item-gacha/spends/{request_id}/{action}",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"guild_id": str(guild_id), "user_id": str(user_id)},
            ) as response:
                if response.status == 204:
                    return True
                body = await response.text()
                LOGGER.warning(
                    "level-bot item gacha spend %s rejected status=%d body=%s",
                    action,
                    response.status,
                    body[:300],
                )
        except (aiohttp.ClientError, TimeoutError) as error:
            LOGGER.warning("Could not %s item gacha spend: %s", action, error)
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

    @staticmethod
    def _parse_xp_exchange(item: object) -> MinecraftXpExchangeEvent:
        if not isinstance(item, dict):
            raise ValueError("XP exchange event must be an object")
        event = MinecraftXpExchangeEvent(
            id=int(item["id"]),
            event_id=str(item["event_id"]),
            guild_id=int(item["guild_id"]),
            user_id=int(item["user_id"]),
            minecraft_account_id=str(item["minecraft_account_id"]),
            cost_xp=int(item["cost_xp"]),
            reward_xp=int(item["reward_xp"]),
            status=str(item["status"]),
        )
        if (
            event.id <= 0
            or not event.event_id
            or event.guild_id <= 0
            or event.user_id <= 0
            or event.cost_xp <= 0
            or event.reward_xp <= 0
            or event.status not in {"pending", "delivering"}
        ):
            raise ValueError("XP exchange event contains invalid values")
        return event

    @staticmethod
    def _parse_wallet(item: object) -> MinecraftXpWallet:
        if not isinstance(item, dict):
            raise ValueError("XP wallet must be an object")
        wallet = MinecraftXpWallet(
            total_xp=int(item["total_xp"]),
            spent_xp=int(item["spent_xp"]),
            available_xp=int(item["available_xp"]),
        )
        if (
            wallet.total_xp < 0
            or wallet.spent_xp < 0
            or wallet.available_xp < 0
            or wallet.available_xp != max(0, wallet.total_xp - wallet.spent_xp)
        ):
            raise ValueError("XP wallet contains invalid values")
        return wallet

    @staticmethod
    def _parse_pack(item: object) -> MinecraftXpPack:
        if not isinstance(item, dict):
            raise ValueError("XP pack must be an object")
        pack = MinecraftXpPack(
            cost_xp=int(item["cost_xp"]),
            reward_xp=int(item["reward_xp"]),
        )
        if pack.cost_xp <= 0 or pack.reward_xp <= 0:
            raise ValueError("XP pack contains invalid values")
        return pack

    @classmethod
    def _parse_xp_shop(cls, item: object) -> MinecraftXpShop:
        if not isinstance(item, dict) or not isinstance(item.get("packs"), list):
            raise ValueError("XP shop must be an object")
        packs = tuple(cls._parse_pack(pack) for pack in item["packs"])
        if not packs or len(packs) > 25 or len({pack.cost_xp for pack in packs}) != len(packs):
            raise ValueError("XP shop packs contain invalid values")
        return MinecraftXpShop(wallet=cls._parse_wallet(item["wallet"]), packs=packs)

    @classmethod
    def _parse_xp_exchange_request(cls, item: object) -> MinecraftXpExchangeRequest:
        if not isinstance(item, dict):
            raise ValueError("XP exchange request must be an object")
        status = str(item["status"])
        if status not in {"reserved", "offline", "insufficient_xp", "unavailable"}:
            raise ValueError("XP exchange request has invalid status")
        pack_item = item.get("pack")
        return MinecraftXpExchangeRequest(
            status=status,
            message=str(item["message"]),
            wallet_before=cls._parse_wallet(item["wallet_before"]),
            wallet_after=cls._parse_wallet(item["wallet_after"]),
            pack=cls._parse_pack(pack_item) if pack_item is not None else None,
        )

    @staticmethod
    def _parse_resource_pack(item: object) -> MinecraftResourcePack:
        if not isinstance(item, dict):
            raise ValueError("resource pack must be an object")
        pack = MinecraftResourcePack(
            item_id=str(item["item_id"]),
            item_name=str(item["item_name"]),
            item_count=int(item["item_count"]),
            cost_xp=int(item["cost_xp"]),
        )
        if (
            pack.item_id not in {"minecraft:diamond", "minecraft:emerald"}
            or pack.item_name != _RESOURCE_ITEM_NAMES.get(pack.item_id)
            or not 1 <= pack.item_count <= 64
            or pack.cost_xp <= 0
        ):
            raise ValueError("resource pack contains invalid values")
        return pack

    @classmethod
    def _parse_resource_shop(cls, item: object) -> MinecraftResourceShop:
        if not isinstance(item, dict) or not isinstance(item.get("packs"), list):
            raise ValueError("resource shop must be an object")
        packs = tuple(cls._parse_resource_pack(pack) for pack in item["packs"])
        identities = {(pack.item_id, pack.item_count) for pack in packs}
        if not packs or len(packs) > 25 or len(identities) != len(packs):
            raise ValueError("resource shop packs contain invalid values")
        return MinecraftResourceShop(wallet=cls._parse_wallet(item["wallet"]), packs=packs)

    @classmethod
    def _parse_resource_exchange_request(cls, item: object) -> MinecraftResourceExchangeRequest:
        if not isinstance(item, dict):
            raise ValueError("resource exchange request must be an object")
        status = str(item["status"])
        if status not in {"reserved", "offline", "insufficient_xp", "unavailable"}:
            raise ValueError("resource exchange request has invalid status")
        pack_item = item.get("pack")
        return MinecraftResourceExchangeRequest(
            status=status,
            message=str(item["message"]),
            wallet_before=cls._parse_wallet(item["wallet_before"]),
            wallet_after=cls._parse_wallet(item["wallet_after"]),
            pack=cls._parse_resource_pack(pack_item) if pack_item is not None else None,
        )

    @classmethod
    def _parse_resource_exchange(cls, item: object) -> MinecraftResourceExchangeEvent:
        if not isinstance(item, dict):
            raise ValueError("resource exchange event must be an object")
        pack = cls._parse_resource_pack(item)
        event = MinecraftResourceExchangeEvent(
            id=int(item["id"]),
            event_id=str(item["event_id"]),
            guild_id=int(item["guild_id"]),
            user_id=int(item["user_id"]),
            minecraft_account_id=str(item["minecraft_account_id"]),
            item_id=pack.item_id,
            item_name=pack.item_name,
            item_count=pack.item_count,
            cost_xp=pack.cost_xp,
            status=str(item["status"]),
        )
        if (
            event.id <= 0
            or not event.event_id
            or event.guild_id <= 0
            or event.user_id <= 0
            or event.status not in {"pending", "delivering"}
        ):
            raise ValueError("resource exchange event contains invalid values")
        return event

    @classmethod
    def _parse_item_gacha_offer(cls, item: object) -> MinecraftItemGachaOffer:
        if not isinstance(item, dict):
            raise ValueError("item gacha offer must be an object")
        offer = MinecraftItemGachaOffer(
            cost_xp=int(item["cost_xp"]),
            normal_cost_xp=int(item["normal_cost_xp"]),
            premium_cost_xp=int(item["premium_cost_xp"]),
            daily_limit=int(item["daily_limit"]),
            wallet=cls._parse_wallet(item["wallet"]),
        )
        if (
            offer.cost_xp <= 0
            or offer.normal_cost_xp <= 0
            or offer.premium_cost_xp <= 0
            or offer.daily_limit <= 0
        ):
            raise ValueError("item gacha offer has invalid cost")
        return offer

    @classmethod
    def _parse_item_gacha_spend_request(cls, item: object) -> MinecraftItemGachaSpendRequest:
        if not isinstance(item, dict):
            raise ValueError("item gacha spend must be an object")
        status = str(item["status"])
        if status not in {
            "reserved",
            "completed",
            "offline",
            "insufficient_xp",
            "unavailable",
        }:
            raise ValueError("item gacha spend has invalid status")
        result = MinecraftItemGachaSpendRequest(
            status=status,
            message=str(item["message"]),
            cost_xp=int(item["cost_xp"]),
            wallet_before=cls._parse_wallet(item["wallet_before"]),
            wallet_after=cls._parse_wallet(item["wallet_after"]),
        )
        if result.cost_xp <= 0:
            raise ValueError("item gacha spend has invalid cost")
        return result
