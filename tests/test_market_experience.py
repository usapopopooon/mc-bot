import asyncio
from typing import Any

from mc_bot.experience import LevelBotXpClient

REQUEST_ID = "44444444-4444-4444-8444-444444444444"


class FakeResponse:
    def __init__(self, payload: dict[str, object], *, status: int = 200) -> None:
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

    def __init__(
        self,
        *,
        market_status: int = 200,
        market_payload: dict[str, object] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.market_status = market_status
        self.market_payload = market_payload or {
            "status": "reserved",
            "message": "購入を予約しました。",
            "request_id": REQUEST_ID,
            "duplicate": False,
            "wallet_before": {
                "total_xp": 5_000,
                "spent_xp": 500,
                "available_xp": 4_500,
            },
            "wallet_after": {
                "total_xp": 5_000,
                "spent_xp": 3_500,
                "available_xp": 1_500,
            },
        }

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return FakeResponse({"wallet": {"total_xp": 5_000, "spent_xp": 500, "available_xp": 4_500}})

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if url.endswith(("/complete", "/cancel")):
            return FakeResponse({}, status=204)
        return FakeResponse(self.market_payload, status=self.market_status)


def test_market_api_wires_exact_parties_accounts_and_price() -> None:
    async def exercise() -> None:
        client = LevelBotXpClient("https://levels.example.test", "secret")
        session = FakeSession()
        client._session = session  # type: ignore[assignment]

        wallet = await client.fetch_market_wallet(1001, 2003)
        result = await client.request_market_purchase(
            request_id=REQUEST_ID,
            guild_id=1001,
            listing_id=17,
            buyer_user_id=2003,
            seller_user_id=2002,
            buyer_account_id=3,
            seller_account_id=2,
            expected_cost_xp=3_000,
        )
        completed = await client.update_market_purchase(
            request_id=REQUEST_ID,
            guild_id=1001,
            action="complete",
        )

        assert wallet is not None and wallet.available_xp == 4_500
        assert result is not None and result.wallet_after.available_xp == 1_500
        assert completed
        assert session.calls[1]["json"] == {
            "request_id": REQUEST_ID,
            "guild_id": "1001",
            "listing_id": 17,
            "buyer_user_id": "2003",
            "seller_user_id": "2002",
            "buyer_minecraft_account_id": "mc-bot:3",
            "seller_minecraft_account_id": "mc-bot:2",
            "expected_cost_xp": 3_000,
        }
        assert session.calls[2]["json"] == {"guild_id": "1001"}

    asyncio.run(exercise())


def test_market_api_accepts_identified_duplicate_conflict() -> None:
    async def exercise() -> None:
        session = FakeSession(
            market_status=409,
            market_payload={
                "status": "reserved",
                "message": "この購入は受付済みです。",
                "request_id": REQUEST_ID,
                "duplicate": True,
                "wallet_before": {
                    "total_xp": 5_000,
                    "spent_xp": 500,
                    "available_xp": 4_500,
                },
                "wallet_after": {
                    "total_xp": 5_000,
                    "spent_xp": 3_500,
                    "available_xp": 1_500,
                },
            },
        )
        client = LevelBotXpClient("https://levels.example.test", "secret")
        client._session = session  # type: ignore[assignment]

        result = await client.request_market_purchase(
            request_id=REQUEST_ID,
            guild_id=1001,
            listing_id=17,
            buyer_user_id=2003,
            seller_user_id=2002,
            buyer_account_id=3,
            seller_account_id=2,
            expected_cost_xp=3_000,
        )

        assert result is not None
        assert result.status == "reserved"
        assert result.request_id == REQUEST_ID
        assert result.duplicate is True

    asyncio.run(exercise())


def test_market_api_preserves_purchase_conflict_response() -> None:
    async def exercise() -> None:
        session = FakeSession(
            market_status=409,
            market_payload={
                "status": "conflict",
                "message": "同じ操作IDが別の購入に使用されています。",
                "request_id": None,
                "duplicate": False,
                "wallet_before": {
                    "total_xp": 5_000,
                    "spent_xp": 500,
                    "available_xp": 4_500,
                },
                "wallet_after": {
                    "total_xp": 5_000,
                    "spent_xp": 500,
                    "available_xp": 4_500,
                },
            },
        )
        client = LevelBotXpClient("https://levels.example.test", "secret")
        client._session = session  # type: ignore[assignment]

        result = await client.request_market_purchase(
            request_id=REQUEST_ID,
            guild_id=1001,
            listing_id=17,
            buyer_user_id=2003,
            seller_user_id=2002,
            buyer_account_id=3,
            seller_account_id=2,
            expected_cost_xp=3_000,
        )

        assert result is not None
        assert result.status == "conflict"
        assert result.duplicate is False

    asyncio.run(exercise())


def test_market_api_rejects_unidentified_reserved_conflict() -> None:
    async def exercise() -> None:
        session = FakeSession(market_status=409)
        client = LevelBotXpClient("https://levels.example.test", "secret")
        client._session = session  # type: ignore[assignment]

        result = await client.request_market_purchase(
            request_id=REQUEST_ID,
            guild_id=1001,
            listing_id=17,
            buyer_user_id=2003,
            seller_user_id=2002,
            buyer_account_id=3,
            seller_account_id=2,
            expected_cost_xp=3_000,
        )

        assert result is None

    asyncio.run(exercise())
