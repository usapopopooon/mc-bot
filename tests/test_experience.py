import asyncio
import json
from typing import Any

import pytest

from mc_bot.accounts import FishingComboRewardEvent, WoodcuttingComboRewardEvent
from mc_bot.experience import (
    LevelBotXpClient,
    MinecraftLevelUpEvent,
    actionbar_clear_command,
    advancement_reward_tellraw_command,
    experience_add_points_command,
    experience_query_command,
    experience_to_next_level,
    level_up_tellraw_command,
    parse_experience_query,
    server_xp_started_tellraw_command,
    total_experience_points,
    voice_bonus_started_tellraw_command,
    xp_exchange_tellraw_command,
)


class _FakeResponse:
    status = 200

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return {
            "status": "reserved",
            "message": "交換を受け付けました。",
            "wallet_before": {
                "total_xp": 100,
                "spent_xp": 0,
                "available_xp": 100,
            },
            "wallet_after": {
                "total_xp": 100,
                "spent_xp": 10,
                "available_xp": 90,
            },
            "pack": {"cost_xp": 10, "reward_xp": 50},
        }


class _FakeSession:
    closed = False

    def __init__(self) -> None:
        self.post_kwargs: dict[str, Any] | None = None

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.post_kwargs = {"url": url, **kwargs}
        return _FakeResponse()


def test_parses_level_up_and_xp_exchange_api_events() -> None:
    level_up = LevelBotXpClient._parse_level_up_event(
        {
            "id": 1,
            "guild_id": "1001",
            "guild_name": "うさぽサーバー",
            "user_id": "2001",
            "display_name": "Steve",
            "level": 2,
            "minecraft_delivered": False,
            "discord_delivered": False,
        }
    )
    exchange = LevelBotXpClient._parse_xp_exchange(
        {
            "id": 2,
            "event_id": "exchange-uuid",
            "guild_id": "1001",
            "user_id": "2001",
            "minecraft_account_id": "mc-bot:1",
            "cost_xp": 10,
            "reward_xp": 50,
            "status": "pending",
        }
    )

    assert level_up.level == 2
    assert exchange.event_id == "exchange-uuid"
    assert exchange.reward_xp == 50


def test_builds_private_actionbar_clear_command() -> None:
    assert actionbar_clear_command("Steve") == 'title Steve actionbar {"text":""}'
    with pytest.raises(ValueError):
        actionbar_clear_command("@a")


def test_parses_minecraft_xp_shop_and_exchange_request() -> None:
    shop = LevelBotXpClient._parse_xp_shop(
        {
            "wallet": {"total_xp": 100, "spent_xp": 10, "available_xp": 90},
            "packs": [
                {"cost_xp": 10, "reward_xp": 50},
                {"cost_xp": 50, "reward_xp": 250},
            ],
        }
    )
    result = LevelBotXpClient._parse_xp_exchange_request(
        {
            "status": "reserved",
            "message": "交換を受け付けました。",
            "wallet_before": {
                "total_xp": 100,
                "spent_xp": 10,
                "available_xp": 90,
            },
            "wallet_after": {
                "total_xp": 100,
                "spent_xp": 20,
                "available_xp": 80,
            },
            "pack": {"cost_xp": 10, "reward_xp": 50},
        }
    )

    assert shop.wallet.available_xp == 90
    assert shop.packs[1].reward_xp == 250
    assert result.status == "reserved"
    assert result.wallet_after.available_xp == 80


def test_exchange_request_sends_idempotency_id_and_expected_rate() -> None:
    async def exercise() -> None:
        client = LevelBotXpClient("https://levels.example.test", "secret")
        session = _FakeSession()
        client._session = session  # type: ignore[assignment]

        result = await client.request_xp_exchange(
            1001,
            2001,
            "00000000-0000-4000-8000-000000000001",
            10,
            50,
        )

        assert result is not None
        assert session.post_kwargs is not None
        assert session.post_kwargs["json"] == {
            "request_id": "00000000-0000-4000-8000-000000000001",
            "guild_id": "1001",
            "user_id": "2001",
            "cost_xp": 10,
            "expected_reward_xp": 50,
        }

    asyncio.run(exercise())


def test_fishing_combo_sends_audit_payload_without_server_xp_fields() -> None:
    async def exercise() -> None:
        client = LevelBotXpClient("https://levels.example.test", "secret")
        session = _FakeSession()
        client._session = session  # type: ignore[assignment]
        event = FishingComboRewardEvent(
            event_id="fish-2",
            account_id=7,
            discord_user_id=2001,
            guild_id=1001,
            catch_count=2,
            combo_count=2,
            reward_xp=5,
            observed_at="2026-08-09T00:00:00+00:00",
            reward_delivered=True,
            audit_delivered=False,
        )

        assert await client.send_fishing_combo(event)
        assert session.post_kwargs is not None
        assert session.post_kwargs["url"].endswith("/fishing-combo-events")
        assert session.post_kwargs["json"] == {
            "event_id": "fish-2",
            "guild_id": "1001",
            "user_id": "2001",
            "minecraft_account_id": "mc-bot:7",
            "catch_count": 2,
            "combo_count": 2,
            "reward_xp": 5,
            "observed_at": "2026-08-09T00:00:00+00:00",
        }

    asyncio.run(exercise())


def test_woodcutting_combo_sends_audit_payload_without_server_xp_fields() -> None:
    async def exercise() -> None:
        client = LevelBotXpClient("https://levels.example.test", "secret")
        session = _FakeSession()
        client._session = session  # type: ignore[assignment]
        event = WoodcuttingComboRewardEvent(
            event_id="wood-5",
            account_id=7,
            discord_user_id=2001,
            guild_id=1001,
            log_count=105,
            combo_count=5,
            reward_xp=5,
            observed_at="2026-08-11T00:00:00+00:00",
            reward_delivered=True,
            audit_delivered=False,
        )
        assert await client.send_woodcutting_combo(event)
        assert session.post_kwargs is not None
        assert session.post_kwargs["url"].endswith("/woodcutting-combo-events")
        assert session.post_kwargs["json"] == {
            "event_id": "wood-5",
            "guild_id": "1001",
            "user_id": "2001",
            "minecraft_account_id": "mc-bot:7",
            "log_count": 105,
            "combo_count": 5,
            "reward_xp": 5,
            "observed_at": "2026-08-11T00:00:00+00:00",
        }

    asyncio.run(exercise())


def test_rejects_inconsistent_minecraft_xp_shop_wallet() -> None:
    with pytest.raises(ValueError):
        LevelBotXpClient._parse_xp_shop(
            {
                "wallet": {"total_xp": 100, "spent_xp": 10, "available_xp": 100},
                "packs": [{"cost_xp": 10, "reward_xp": 50}],
            }
        )


def test_parses_experience_query_responses() -> None:
    assert parse_experience_query("Steve has 30 experience levels", "levels") == 30
    assert parse_experience_query("Steve has 55 experience points", "points") == 55


def test_rejects_wrong_or_unreadable_query_unit() -> None:
    with pytest.raises(ValueError):
        parse_experience_query("Steve has 30 experience levels", "points")
    with pytest.raises(ValueError):
        parse_experience_query("No player was found", "levels")


@pytest.mark.parametrize(
    ("level", "expected"),
    [(0, 0), (1, 7), (16, 352), (17, 394), (30, 1395), (31, 1507), (32, 1628)],
)
def test_calculates_total_experience_at_level_floor(level: int, expected: int) -> None:
    assert total_experience_points(level, 0) == expected


def test_adds_points_within_current_level() -> None:
    assert total_experience_points(30, 55) == 1450


def test_builds_safe_experience_add_points_command() -> None:
    assert experience_add_points_command(".Bedrock_User", 25) == (
        "experience add .Bedrock_User 25 points"
    )


def test_rejects_invalid_experience_add_points_command() -> None:
    with pytest.raises(ValueError):
        experience_add_points_command("@a", 25)
    with pytest.raises(ValueError):
        experience_add_points_command("Steve", 0)
    assert experience_to_next_level(30) == 112


def test_rejects_inconsistent_level_progress() -> None:
    with pytest.raises(ValueError):
        total_experience_points(30, 112)


def test_builds_safe_player_query_command() -> None:
    assert experience_query_command("Steve", "levels") == "experience query Steve levels"
    assert experience_query_command(".Bedrock_User", "points") == (
        "experience query .Bedrock_User points"
    )
    with pytest.raises(ValueError):
        experience_query_command("Steve run kill @a", "levels")


def test_builds_level_up_tellraw_with_discord_guild_name() -> None:
    event = MinecraftLevelUpEvent(
        id=1,
        guild_id=1001,
        guild_name='うさぽ"サーバー',
        user_id=2001,
        display_name="うさぽ",
        level=10,
        minecraft_delivered=False,
        discord_delivered=False,
    )

    command = level_up_tellraw_command(event)
    components = json.loads(command.removeprefix("tellraw @a "))

    assert components[1] == {"text": 'うさぽ"サーバー', "color": "aqua"}
    assert components[3] == {"text": "うさぽ", "color": "yellow"}
    assert components[5] == {"text": "10", "color": "green", "bold": True}


def test_builds_separate_advancement_reward_tellraw() -> None:
    command = advancement_reward_tellraw_command(
        'うさぽ"サーバー',
        'Steve"',
        "Stone Age",
        100,
        100,
    )
    components = json.loads(command.removeprefix("tellraw @a "))

    assert components[1] == {"text": 'うさぽ"サーバー', "color": "aqua"}
    assert components[3] == {"text": 'Steve"', "color": "yellow"}
    assert components[5] == {"text": "Stone Age", "color": "gold"}
    assert components[7] == {"text": "100 XP", "color": "green", "bold": True}
    assert components[9] == {"text": "100 XP", "color": "green", "bold": True}


def test_builds_voice_bonus_started_tellraw_like_level_up_message() -> None:
    command = voice_bonus_started_tellraw_command("うさぽサーバー", "Steve")
    components = json.loads(command.removeprefix("tellraw @a "))

    assert components[1] == {"text": "うさぽサーバー", "color": "aqua"}
    assert components[3] == {"text": "Steve", "color": "yellow"}
    assert components[5] == {
        "text": "VC XPとMinecraft内の経験値が2倍",
        "color": "green",
        "bold": True,
    }


def test_builds_server_xp_started_tellraw() -> None:
    command = server_xp_started_tellraw_command("うさぽサーバー", "Steve")
    components = json.loads(command.removeprefix("tellraw @a "))

    assert components[1] == {"text": "うさぽサーバー", "color": "aqua"}
    assert components[3] == {"text": "Steve", "color": "yellow"}
    assert components[5] == {
        "text": "サーバーXP",
        "color": "green",
        "bold": True,
    }
    assert components[6] == {"text": "を獲得します!"}


def test_builds_minecraft_xp_exchange_tellraw() -> None:
    command = xp_exchange_tellraw_command("うさぽサーバー", "Steve", 10, 100)
    components = json.loads(command.removeprefix("tellraw @a "))

    assert components[1] == {"text": "うさぽサーバー", "color": "aqua"}
    assert components[3] == {"text": "Steve", "color": "yellow"}
    assert components[5] == {"text": "10", "color": "green", "bold": True}
    assert components[7] == {"text": "100 XP", "color": "green", "bold": True}
