import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import MinecraftMarketPurchaseRequest, MinecraftXpWallet
from mc_bot.quest_request import MinecraftQuestStateEvent
from mc_bot.settings import RuntimeSettings

DEPARTING_UUID = "22222222-2222-4222-8222-222222222222"
OTHER_UUID = "33333333-3333-4333-8333-333333333333"


def _quest_event(
    *,
    quest_id: int,
    event_id: str,
    transition_id: str,
    owner_uuid: str,
    owner_name: str,
    worker_uuid: str,
    worker_name: str,
) -> MinecraftQuestStateEvent:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    return MinecraftQuestStateEvent(
        transition_id=transition_id,
        transition_kind="accepted",
        quest_id=quest_id,
        event_id=event_id,
        owner_uuid=owner_uuid,
        owner_name=owner_name,
        worker_uuid=worker_uuid,
        worker_name=worker_name,
        requested_item_id="minecraft:stone",
        requested_item_name="石",
        requested_count=32,
        reward_item_id="minecraft:diamond",
        reward_item_name="ダイヤモンド",
        reward_count=1,
        fulfillment_hours=24,
        status="accepted",
        open_expires_at=(now + timedelta(days=7)).isoformat(),
        accepted_deadline=(now + timedelta(days=1)).isoformat(),
        created_at=now.isoformat(),
        published_at=now.isoformat(),
    )


def test_departure_returns_market_listing_and_reconciles_both_quest_roles(tmp_path) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="test", accounts_path=tmp_path / "state.db"))
    bot._accounts.initialize()
    bot._market.initialize()
    bot._quests.initialize()
    departing = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Departing",
        server_player_name="Departing",
        discord_user_id=2002,
        discord_username="departing",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=DEPARTING_UUID,
    )
    other = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Other",
        server_player_name="Other",
        discord_user_id=2003,
        discord_username="other",
        source="self",
        status="active",
        created_by=2003,
        player_uuid=OTHER_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id="11111111-1111-4111-8111-111111111111",
        seller_account_id=departing.id,
        seller_discord_user_id=2002,
        seller_uuid=DEPARTING_UUID,
        seller_name="Departing",
        item_id="minecraft:diamond",
        item_name="ダイヤモンド",
        item_count=3,
        price_xp=500,
        created_at=datetime.now(UTC).isoformat(),
    )
    owner_event = _quest_event(
        quest_id=21,
        event_id="44444444-4444-4444-8444-444444444444",
        transition_id="55555555-5555-4555-8555-555555555555",
        owner_uuid=DEPARTING_UUID,
        owner_name="Departing",
        worker_uuid=OTHER_UUID,
        worker_name="Other",
    )
    worker_event = _quest_event(
        quest_id=22,
        event_id="66666666-6666-4666-8666-666666666666",
        transition_id="77777777-7777-4777-8777-777777777777",
        owner_uuid=OTHER_UUID,
        owner_name="Other",
        worker_uuid=DEPARTING_UUID,
        worker_name="Departing",
    )
    bot._quests.apply_state(
        owner_event,
        owner_account_id=departing.id,
        owner_discord_user_id=2002,
        worker_account_id=other.id,
        worker_discord_user_id=2003,
    )
    bot._quests.apply_state(
        worker_event,
        owner_account_id=other.id,
        owner_discord_user_id=2003,
        worker_account_id=departing.id,
        worker_discord_user_id=2002,
    )
    commands: list[str] = []

    async def execute(command: str) -> str:
        commands.append(command)
        fields = command.split()
        if fields[1] == "market-mailbox-return":
            return f"USAPO_MARKET_TRANSFER_RESULT|1|{fields[4]}|{fields[2]}|completed|cancelled|new"
        quest_status = "cancelled" if fields[1] == "quest-invalidate" else "open"
        return f"USAPO_QUEST_ACTION_RESULT|1|{fields[4]}|{fields[2]}|completed|{quest_status}|new"

    bot._execute_rcon = execute  # type: ignore[method-assign]
    bot._refresh_market_listing = AsyncMock()  # type: ignore[method-assign]
    bot._refresh_market_panel = AsyncMock()  # type: ignore[method-assign]
    bot._try_deliver_market_cancellation_logs = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(bot._revoke_account_exchange_state(departing, player_uuid=DEPARTING_UUID))

    listing = bot._market.get(17)
    assert listing is not None
    assert listing.status == "cancelled"
    assert commands[0].split()[1:4] == ["market-mailbox-return", "17", DEPARTING_UUID]
    quest_commands = {command.split()[1]: command.split()[1:4] for command in commands[1:]}
    assert quest_commands["quest-invalidate"] == [
        "quest-invalidate",
        "21",
        DEPARTING_UUID,
    ]
    assert quest_commands["quest-abandon"] == ["quest-abandon", "22", DEPARTING_UUID]


def test_ambiguous_market_mailbox_return_retries_the_same_operation(tmp_path) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="test", accounts_path=tmp_path / "state.db"))
    bot._accounts.initialize()
    bot._market.initialize()
    bot._quests.initialize()
    departing = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Departing",
        server_player_name="Departing",
        discord_user_id=2002,
        discord_username="departing",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=DEPARTING_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id="11111111-1111-4111-8111-111111111111",
        seller_account_id=departing.id,
        seller_discord_user_id=2002,
        seller_uuid=DEPARTING_UUID,
        seller_name="Departing",
        item_id="minecraft:diamond",
        item_name="ダイヤモンド",
        item_count=3,
        price_xp=500,
        created_at=datetime.now(UTC).isoformat(),
    )
    commands: list[str] = []

    async def ambiguous(command: str) -> str:
        commands.append(command)
        raise RuntimeError("lost RCON response")

    bot._execute_rcon = ambiguous  # type: ignore[method-assign]
    bot._refresh_market_listing = AsyncMock()  # type: ignore[method-assign]
    bot._refresh_market_panel = AsyncMock()  # type: ignore[method-assign]
    bot._try_deliver_market_cancellation_logs = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="lost RCON response"):
        asyncio.run(bot._revoke_account_exchange_state(departing, player_uuid=DEPARTING_UUID))
    pending = bot._market.get(17)
    assert pending is not None
    assert pending.status == "cancelling"

    async def completed(command: str) -> str:
        commands.append(command)
        fields = command.split()
        return (
            f"USAPO_MARKET_TRANSFER_RESULT|1|{fields[4]}|{fields[2]}|completed|cancelled|duplicate"
        )

    bot._execute_rcon = completed  # type: ignore[method-assign]
    asyncio.run(bot._revoke_account_exchange_state(departing, player_uuid=DEPARTING_UUID))

    assert commands[0] == commands[1]
    completed_listing = bot._market.get(17)
    assert completed_listing is not None
    assert completed_listing.status == "cancelled"


def test_departure_resolves_reserved_purchase_before_returning_listing(tmp_path) -> None:
    bot = MinecraftDiscordBot(Config(discord_token="test", accounts_path=tmp_path / "state.db"))
    bot._accounts.initialize()
    bot._market.initialize()
    bot._quests.initialize()
    bot._settings = RuntimeSettings(guild_id=1001)
    departing = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Departing",
        server_player_name="Departing",
        discord_user_id=2002,
        discord_username="departing",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=DEPARTING_UUID,
    )
    buyer = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Buyer",
        server_player_name="Buyer",
        discord_user_id=2003,
        discord_username="buyer",
        source="self",
        status="active",
        created_by=2003,
        player_uuid=OTHER_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id="11111111-1111-4111-8111-111111111111",
        seller_account_id=departing.id,
        seller_discord_user_id=2002,
        seller_uuid=DEPARTING_UUID,
        seller_name="Departing",
        item_id="minecraft:diamond",
        item_name="ダイヤモンド",
        item_count=3,
        price_xp=500,
        created_at=datetime.now(UTC).isoformat(),
    )
    purchase_id = "88888888-8888-4888-8888-888888888888"
    reserved = bot._market.reserve_purchase(
        listing_id=17,
        request_id=purchase_id,
        expected_price_xp=500,
        buyer_account_id=buyer.id,
        buyer_discord_user_id=2003,
    )
    assert reserved is not None
    wallet = MinecraftXpWallet(total_xp=1_000, spent_xp=0, available_xp=1_000)
    bot._level_bot_xp.request_market_purchase = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftMarketPurchaseRequest(
            status="conflict",
            message="購入予約は解除済みです。",
            request_id=None,
            wallet_before=wallet,
            wallet_after=wallet,
        )
    )
    commands: list[str] = []

    async def execute(command: str) -> str:
        commands.append(command)
        fields = command.split()
        return f"USAPO_MARKET_TRANSFER_RESULT|1|{fields[4]}|{fields[2]}|completed|cancelled|new"

    bot._execute_rcon = execute  # type: ignore[method-assign]
    bot._refresh_market_listing = AsyncMock()  # type: ignore[method-assign]
    bot._refresh_market_panel = AsyncMock()  # type: ignore[method-assign]
    bot._try_deliver_market_cancellation_logs = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(bot._revoke_account_exchange_state(departing, player_uuid=DEPARTING_UUID))

    bot._level_bot_xp.request_market_purchase.assert_awaited_once_with(  # type: ignore[attr-defined]
        request_id=purchase_id,
        guild_id=1001,
        listing_id=17,
        buyer_user_id=2003,
        seller_user_id=2002,
        buyer_account_id=buyer.id,
        seller_account_id=departing.id,
        expected_cost_xp=500,
    )
    listing = bot._market.get(17)
    assert listing is not None
    assert listing.status == "cancelled"
    assert any(" market-mailbox-return 17 " in command for command in commands)


def test_whitelist_access_is_removed_before_exchange_cleanup_retry(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text(
        json.dumps([{"uuid": DEPARTING_UUID, "name": "Departing"}]),
        encoding="utf-8",
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "state.db",
            minecraft_whitelist_path=whitelist_path,
            rcon_password="test",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    bot._quests.initialize()
    departing = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Departing",
        server_player_name="Departing",
        discord_user_id=2002,
        discord_username="departing",
        source="self",
        status="active",
        created_by=2002,
        player_uuid=DEPARTING_UUID,
    )
    bot._market.add_listing(
        listing_id=17,
        event_id="11111111-1111-4111-8111-111111111111",
        seller_account_id=departing.id,
        seller_discord_user_id=2002,
        seller_uuid=DEPARTING_UUID,
        seller_name="Departing",
        item_id="minecraft:diamond",
        item_name="ダイヤモンド",
        item_count=3,
        price_xp=500,
        created_at=datetime.now(UTC).isoformat(),
    )
    bot._resolve_java_profile_by_uuid = AsyncMock(  # type: ignore[method-assign]
        return_value=("Departing", DEPARTING_UUID)
    )

    class RemovalRcon:
        def execute(self, command: str) -> str:
            if command == "whitelist remove Departing":
                whitelist_path.write_text("[]", encoding="utf-8")
                return "Removed Departing from the whitelist"
            if command.startswith('kick "Departing"'):
                return "Kicked Departing"
            if command.startswith("usapo-event-bridge market-mailbox-return "):
                raise OSError("temporary RCON failure")
            raise AssertionError(f"unexpected command: {command}")

    bot._rcon = RemovalRcon()  # type: ignore[assignment]

    with pytest.raises(OSError, match="temporary RCON failure"):
        asyncio.run(bot._remove_from_whitelist(departing))

    assert json.loads(whitelist_path.read_text(encoding="utf-8")) == []
    listing = bot._market.get(17)
    assert listing is not None
    assert listing.status == "cancelling"
