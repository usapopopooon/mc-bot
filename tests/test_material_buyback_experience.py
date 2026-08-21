import asyncio
from typing import Any

from mc_bot.experience import LevelBotXpClient

REQUEST_ID = "55555555-5555-4555-8555-555555555555"


class FakeResponse:
    def __init__(self, payload: dict[str, object], *, status: int) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return self.payload

    async def text(self) -> str:
        return ""


class FakeSession:
    closed = False

    def __init__(self, *, reserve_status: int = 200, duplicate: bool = False) -> None:
        self.reserve_status = reserve_status
        self.duplicate = duplicate
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if url.endswith(("/complete", "/cancel")):
            return FakeResponse({}, status=204)
        return FakeResponse(
            {
                "status": "reserved",
                "message": "砂岩の買取を受け付けました。",
                "request_id": REQUEST_ID,
                "item_id": "minecraft:sandstone",
                "item_name": "砂岩",
                "item_count": 256,
                "reward_xp": 200,
                "reward_day": "2026-08-21",
                "daily_reserved_xp": 450,
                "daily_limit_xp": 1_500,
                "duplicate": self.duplicate,
            },
            status=self.reserve_status,
        )


def test_material_buyback_api_wires_exact_account_item_reward_and_completion() -> None:
    async def exercise() -> None:
        client = LevelBotXpClient("https://levels.example.test", "secret")
        session = FakeSession()
        client._session = session  # type: ignore[assignment]

        result = await client.request_material_buyback(
            request_id=REQUEST_ID,
            guild_id=1001,
            user_id=2003,
            account_id=17,
            item_id="minecraft:sandstone",
            item_count=256,
            expected_reward_xp=200,
        )
        completed = await client.update_material_buyback(
            request_id=REQUEST_ID,
            guild_id=1001,
            user_id=2003,
            action="complete",
        )

        assert result is not None and result.reward_xp == 200
        assert completed
        assert session.calls[0]["json"] == {
            "request_id": REQUEST_ID,
            "guild_id": "1001",
            "user_id": "2003",
            "minecraft_account_id": "mc-bot:17",
            "item_id": "minecraft:sandstone",
            "item_count": 256,
            "expected_reward_xp": 200,
        }
        assert session.calls[1]["json"] == {
            "guild_id": "1001",
            "user_id": "2003",
        }

    asyncio.run(exercise())


def test_material_buyback_api_accepts_only_identified_duplicate_conflict() -> None:
    async def exercise() -> None:
        client = LevelBotXpClient("https://levels.example.test", "secret")
        session = FakeSession(reserve_status=409, duplicate=True)
        client._session = session  # type: ignore[assignment]

        result = await client.request_material_buyback(
            request_id=REQUEST_ID,
            guild_id=1001,
            user_id=2003,
            account_id=17,
            item_id="minecraft:sandstone",
            item_count=256,
            expected_reward_xp=200,
        )

        assert result is not None
        assert result.request_id == REQUEST_ID
        assert result.duplicate is True

    asyncio.run(exercise())
