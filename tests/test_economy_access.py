import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.economy_access import (
    ECONOMY_UNAVAILABLE_MESSAGE,
    WORLD_RESTRICTED_MESSAGE,
    economy_access_command,
    guarded_economy_delivery_command,
    parse_economy_access_result,
)
from mc_bot.exchange_request import MinecraftExchangeRequest
from mc_bot.experience import (
    MinecraftItemGachaSpendRequest,
    MinecraftResourceExchangeEvent,
    MinecraftResourcePack,
    MinecraftResourceShop,
    MinecraftXpExchangeEvent,
    MinecraftXpPack,
    MinecraftXpShop,
    MinecraftXpWallet,
)
from mc_bot.item_gacha import get_item_gacha_reward, item_gacha_day
from mc_bot.item_gacha_request import MinecraftItemGachaRequest
from mc_bot.resource_shop import (
    DiamondEmeraldPackSelect,
    EmeraldDiamondPackSelect,
    MinecraftResourceConfirmView,
    MinecraftResourcePackSelectView,
)
from mc_bot.settings import RuntimeSettings
from mc_bot.xp_shop import MinecraftXpConfirmView, MinecraftXpPackSelectView

PLAYER_UUID = "22222222-2222-4222-8222-222222222222"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
GUARD = (
    f"execute as {PLAYER_UUID} at @s unless dimension minecraft:resource "
    "unless dimension minecraft:world_2_nether "
    "unless dimension minecraft:world_2_the_end run "
)


class WorldRcon:
    def __init__(self, world: str = "resource") -> None:
        self.world = world
        self.commands: list[str] = []
        self.delivered: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        restricted = self.world in {"resource", "world_2_nether", "world_2_the_end"}
        if command.startswith("usapo-event-bridge economy-access "):
            _, _, player_uuid, request_id = command.split()
            assert player_uuid == PLAYER_UUID
            UUID(request_id)
            status = "world_restricted" if restricted else "allowed"
            if self.world == "offline":
                status = "player_offline"
            return f"USAPO_ECONOMY_ACCESS_RESULT|1|{request_id}|{status}"
        if command.startswith("execute as "):
            assert command.startswith(GUARD)
            if restricted:
                return ""
            self.delivered.append(command)
            return "Gave 64 [Iron Ingot] to Steve"
        if command.startswith("tellraw "):
            return ""
        raise AssertionError(f"unexpected RCON command: {command}")


def _bot(tmp_path, world="resource"):
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
            level_bot_api_url="http://level-bot",
            level_bot_api_token="test",
        )
    )
    bot._accounts.initialize()
    bot._market.initialize()
    bot._quests.initialize()
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="test",
        source="self",
        status="active",
        created_by=123,
        player_uuid=PLAYER_UUID,
    )
    bot._settings = RuntimeSettings(guild_id=456)
    bot._rcon = WorldRcon(world)  # type: ignore[assignment]
    bot._online_exchange_account = AsyncMock(return_value=(account, None))  # type: ignore[method-assign]
    bot._flush_minecraft_item_gacha_notifications = AsyncMock()  # type: ignore[method-assign]
    wallet = MinecraftXpWallet(200, 0, 200)
    bot._level_bot_xp.request_item_gacha_spend = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftItemGachaSpendRequest("reserved", "予約しました", 100, wallet, wallet)
    )
    bot._level_bot_xp.update_item_gacha_spend = AsyncMock(return_value=True)  # type: ignore[method-assign]
    bot._level_bot_xp.request_xp_exchange = AsyncMock()  # type: ignore[method-assign]
    bot._level_bot_xp.request_resource_exchange = AsyncMock()  # type: ignore[method-assign]
    bot._level_bot_xp.request_market_purchase = AsyncMock()  # type: ignore[method-assign]
    return bot, account


def _interaction():
    return SimpleNamespace(
        guild_id=456,
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.parametrize("status", ["allowed", "world_restricted", "player_offline"])
def test_access_protocol_checks_request_and_status(status) -> None:
    assert economy_access_command(PLAYER_UUID, REQUEST_ID) == (
        f"usapo-event-bridge economy-access {PLAYER_UUID} {REQUEST_ID}"
    )
    assert (
        parse_economy_access_result(
            f"USAPO_ECONOMY_ACCESS_RESULT|1|{REQUEST_ID}|{status}", request_id=REQUEST_ID
        )
        == status
    )


@pytest.mark.parametrize(
    "response",
    [
        "",
        f"USAPO_ECONOMY_ACCESS_RESULT|2|{REQUEST_ID}|allowed",
        f"USAPO_ECONOMY_ACCESS_RESULT|1|{PLAYER_UUID}|allowed",
        f"USAPO_ECONOMY_ACCESS_RESULT|1|{REQUEST_ID}|unknown",
        f"USAPO_ECONOMY_ACCESS_RESULT|1|{REQUEST_ID}|allowed|extra",
    ],
)
def test_access_protocol_fails_closed(response) -> None:
    with pytest.raises(ValueError):
        parse_economy_access_result(response, request_id=REQUEST_ID)


@pytest.mark.parametrize("world", ["resource", "world_2_nether", "world_2_the_end"])
@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("show_minecraft_item_gacha_kind_selection", ("all",)),
        ("show_minecraft_item_gacha_confirmation", ("normal",)),
        ("show_market_purchase_confirmation", (17,)),
        ("show_market_balance", ()),
        ("show_market_guide", ()),
        ("cancel_market_listing", (17,)),
        ("show_minecraft_xp_shop", ()),
        ("show_minecraft_xp_balance", ()),
        ("show_minecraft_resource_shop", ()),
        ("show_minecraft_resource_balance", ()),
        ("show_emerald_diamond_exchange", ()),
        ("show_my_quests", ()),
        ("show_quest_guide", ()),
        ("show_quest_claim_guide", ()),
        ("accept_quest", (17,)),
        ("submit_quest", (17,)),
        ("abandon_quest", (17,)),
    ],
)
def test_discord_buttons_explain_restriction_before_entering_a_flow(
    tmp_path, world, method, args
) -> None:
    bot, _ = _bot(tmp_path, world)
    interaction = _interaction()
    asyncio.run(getattr(bot, method)(interaction, *args))
    interaction.response.send_message.assert_awaited_once_with(
        WORLD_RESTRICTED_MESSAGE, ephemeral=True
    )
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()
    bot._level_bot_xp.request_market_purchase.assert_not_awaited()


@pytest.mark.parametrize("world", ["resource", "world_2_nether", "world_2_the_end"])
def test_queued_gacha_rechecks_world_before_consuming_draw_or_xp(tmp_path, world) -> None:
    bot, _ = _bot(tmp_path, world)
    request = MinecraftItemGachaRequest(
        REQUEST_ID, PLAYER_UUID, "Steve", "all", "normal", 100, datetime.now(UTC).isoformat()
    )
    asyncio.run(bot._handle_minecraft_item_gacha_request(request))
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()
    assert bot._accounts.get_minecraft_item_gacha_draw_by_id(REQUEST_ID) is None
    assert WORLD_RESTRICTED_MESSAGE in bot._rcon.commands[-1]
    assert bot._rcon.delivered == []


@pytest.mark.parametrize("kind", ["xp", "resource", "balance", "emerald_diamond"])
def test_queued_exchange_rechecks_before_reserving_or_querying_wallet(tmp_path, kind) -> None:
    bot, _ = _bot(tmp_path)
    request = MinecraftExchangeRequest(
        REQUEST_ID,
        PLAYER_UUID,
        "Steve",
        kind,
        "minecraft:diamond",
        3,
        100,
        500,
        datetime.now(UTC).isoformat(),
    )
    asyncio.run(bot._handle_minecraft_exchange_request(request))
    bot._level_bot_xp.request_xp_exchange.assert_not_awaited()
    bot._level_bot_xp.request_resource_exchange.assert_not_awaited()
    assert WORLD_RESTRICTED_MESSAGE in bot._rcon.commands[-1]


@pytest.mark.parametrize("kind", ["xp", "resource"])
def test_discord_exchange_confirmation_rechecks_current_world(tmp_path, kind) -> None:
    bot, _ = _bot(tmp_path)
    interaction = _interaction()
    if kind == "xp":
        result = asyncio.run(
            bot.confirm_minecraft_xp_exchange(
                interaction, request_id=REQUEST_ID, cost_xp=100, expected_reward_xp=500
            )
        )
    else:
        result = asyncio.run(
            bot.confirm_minecraft_resource_exchange(
                interaction,
                request_id=REQUEST_ID,
                item_id="minecraft:diamond",
                item_count=3,
                expected_cost_xp=100,
            )
        )
    assert result == WORLD_RESTRICTED_MESSAGE
    bot._level_bot_xp.request_xp_exchange.assert_not_awaited()
    bot._level_bot_xp.request_resource_exchange.assert_not_awaited()


def test_market_rechecks_before_reserving_inventory_or_wallet(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    bot._market.get = Mock(return_value=SimpleNamespace(status="active"))
    bot._market.reserve_purchase = Mock()
    result = asyncio.run(
        bot._purchase_market(
            guild_id=456, listing_id=17, request_id=REQUEST_ID, expected_price_xp=100, buyer=account
        )
    )
    assert result == WORLD_RESTRICTED_MESSAGE
    bot._market.reserve_purchase.assert_not_called()
    bot._level_bot_xp.request_market_purchase.assert_not_awaited()


def test_unsupported_or_unreachable_access_check_never_draws(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    bot._execute_rcon = AsyncMock(return_value="Unknown command")
    interaction = _interaction()
    asyncio.run(bot.draw_minecraft_item_gacha(interaction, known_account=account))
    interaction.followup.send.assert_awaited_once_with(ECONOMY_UNAVAILABLE_MESSAGE, ephemeral=True)
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()


@pytest.mark.parametrize("world", ["resource", "world_2_nether", "world_2_the_end"])
def test_move_during_gacha_payment_refunds_and_never_delivers_in_restricted_world(tmp_path, world):
    bot, account = _bot(tmp_path, "world")
    spend = bot._level_bot_xp.request_item_gacha_spend.return_value

    async def move_during_payment(**kwargs):
        bot._rcon.world = world
        return spend

    bot._level_bot_xp.request_item_gacha_spend.side_effect = move_during_payment
    with patch("mc_bot.bot.draw_item_gacha_reward", return_value=get_item_gacha_reward("n_iron")):
        asyncio.run(
            bot.draw_minecraft_item_gacha(
                _interaction(), known_account=account, request_id=REQUEST_ID
            )
        )
    assert bot._rcon.delivered == []
    assert GUARD + "give @s minecraft:iron_ingot 64" in bot._rcon.commands
    bot._level_bot_xp.update_item_gacha_spend.assert_awaited_once_with(
        request_id=REQUEST_ID, guild_id=456, user_id=123, action="cancel"
    )
    assert bot._accounts.get_minecraft_item_gacha_draw_by_id(REQUEST_ID).status == "retryable"


def test_completed_gacha_replay_reconciles_without_new_delivery_in_restricted_world(tmp_path):
    bot, account = _bot(tmp_path, "world")

    async def exercise():
        await bot.draw_minecraft_item_gacha(
            _interaction(), known_account=account, request_id=REQUEST_ID
        )
        bot._rcon.world = "resource"
        await bot.draw_minecraft_item_gacha(
            _interaction(), known_account=account, request_id=REQUEST_ID
        )

    with patch("mc_bot.bot.draw_item_gacha_reward", return_value=get_item_gacha_reward("n_iron")):
        asyncio.run(exercise())
    assert len(bot._rcon.delivered) == 1
    bot._level_bot_xp.request_item_gacha_spend.assert_awaited_once()
    assert all(
        c.kwargs["action"] == "complete"
        for c in bot._level_bot_xp.update_item_gacha_spend.await_args_list
    )
    assert (
        bot._accounts.count_minecraft_item_gacha_draws(
            guild_id=456, discord_user_id=123, draw_day=item_gacha_day(datetime.now(UTC))
        )
        == 1
    )


@pytest.mark.parametrize("kind", ["xp", "resource"])
def test_queued_exchange_delivery_cancels_if_player_changed_world(tmp_path, kind) -> None:
    bot, account = _bot(tmp_path)
    if kind == "xp":
        event = MinecraftXpExchangeEvent(7, "exchange-7", 456, 123, "mc-bot:1", 100, 500, "pending")
    else:
        event = MinecraftResourceExchangeEvent(
            7,
            "exchange-7",
            456,
            123,
            "mc-bot:1",
            "minecraft:diamond",
            "ダイヤモンド",
            3,
            100,
            "pending",
        )
    fetch = AsyncMock(return_value=[event])
    update = AsyncMock(return_value=True)
    setattr(bot._level_bot_xp, f"fetch_{kind}_exchanges", fetch)
    setattr(bot._level_bot_xp, f"update_{kind}_exchange", update)
    asyncio.run(
        getattr(bot, f"_sync_minecraft_{kind}_exchanges")(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )
    )
    update.assert_awaited_once_with(7, 456, "cancel", claim_token=None)
    assert bot._rcon.delivered == []


def test_offline_non_inventory_discord_actions_keep_prior_behavior(tmp_path) -> None:
    bot, account = _bot(tmp_path, "offline")
    assert asyncio.run(bot._discord_economy_error(123)) is None
    assert asyncio.run(bot._economy_account_error(account)) is not None


def test_multiple_linked_accounts_cannot_hide_online_creative_account(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    bot._accounts.list_for_discord_user = Mock(
        return_value=[replace(account, id=2, player_uuid=REQUEST_ID), account]
    )
    bot._economy_access_status = AsyncMock(side_effect=["player_offline", "world_restricted"])
    assert asyncio.run(bot._discord_economy_error(123)) == WORLD_RESTRICTED_MESSAGE
    assert [c.args[0] for c in bot._economy_access_status.await_args_list] == [
        REQUEST_ID,
        PLAYER_UUID,
    ]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("give OldName minecraft:diamond 3", "give @s minecraft:diamond 3"),
        ("experience add OldName 500 points", "experience add @s 500 points"),
    ],
)
def test_atomic_delivery_guard_targets_verified_uuid_and_all_three_dimensions(command, expected):
    assert guarded_economy_delivery_command(PLAYER_UUID, command) == GUARD + expected


def test_atomic_guard_rejects_unrelated_commands() -> None:
    with pytest.raises(ValueError):
        guarded_economy_delivery_command(PLAYER_UUID, "say test")


@pytest.mark.parametrize("kind", ["xp", "resource", "emerald", "diamond"])
def test_previously_opened_exchange_selection_buttons_recheck_world(tmp_path, kind) -> None:
    bot, _ = _bot(tmp_path)
    interaction = _interaction()

    async def exercise():
        wallet = MinecraftXpWallet(200, 0, 200)
        if kind == "xp":
            view = MinecraftXpPackSelectView(
                bot, owner_id=123, shop=MinecraftXpShop(wallet, (MinecraftXpPack(100, 500),))
            )
            select = view.children[0]
        elif kind == "resource":
            view = MinecraftResourcePackSelectView(
                bot,
                owner_id=123,
                shop=MinecraftResourceShop(
                    wallet, (MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 3, 100),)
                ),
            )
            select = view.children[0]
        elif kind == "emerald":
            select = EmeraldDiamondPackSelect(bot, owner_id=123)
        else:
            select = DiamondEmeraldPackSelect(bot, owner_id=123)
        await select.callback(interaction)

    asyncio.run(exercise())
    interaction.response.send_message.assert_awaited_once_with(
        WORLD_RESTRICTED_MESSAGE, ephemeral=True
    )


@pytest.mark.parametrize("kind", ["xp", "resource"])
def test_confirm_button_displays_denial_and_does_not_mark_completed(tmp_path, kind) -> None:
    bot, _ = _bot(tmp_path)
    interaction = _interaction()
    interaction.response.edit_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    async def exercise():
        if kind == "xp":
            view = MinecraftXpConfirmView(
                bot,
                owner_id=123,
                request_id=REQUEST_ID,
                cost_xp=100,
                expected_reward_xp=500,
                affordable=True,
            )
        else:
            view = MinecraftResourceConfirmView(
                bot,
                owner_id=123,
                request_id=REQUEST_ID,
                pack=MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 3, 100),
                affordable=True,
            )
        await view.confirm.callback(interaction)
        assert not view._completed
        assert not view.confirm.disabled

    asyncio.run(exercise())
    interaction.followup.send.assert_awaited_once_with(WORLD_RESTRICTED_MESSAGE, ephemeral=True)
    bot._level_bot_xp.request_xp_exchange.assert_not_awaited()
    bot._level_bot_xp.request_resource_exchange.assert_not_awaited()


@pytest.mark.parametrize("kind", ["xp", "resource"])
def test_atomic_exchange_guard_cancels_movement_after_preflight(tmp_path, kind) -> None:
    bot, account = _bot(tmp_path, "world")
    if kind == "xp":
        event = MinecraftXpExchangeEvent(7, "exchange-7", 456, 123, "mc-bot:1", 100, 500, "pending")
        bot._query_player_experience = AsyncMock(return_value=0)
    else:
        event = MinecraftResourceExchangeEvent(
            7,
            "exchange-7",
            456,
            123,
            "mc-bot:1",
            "minecraft:diamond",
            "ダイヤモンド",
            3,
            100,
            "pending",
        )

    async def update(_event_id, _guild_id, action, **kwargs):
        if action == "claim":
            bot._rcon.world = "resource"
        return True

    updater = AsyncMock(side_effect=update)
    setattr(bot._level_bot_xp, f"fetch_{kind}_exchanges", AsyncMock(return_value=[event]))
    setattr(bot._level_bot_xp, f"update_{kind}_exchange", updater)
    asyncio.run(
        getattr(bot, f"_sync_minecraft_{kind}_exchanges")(
            guild_id=456, online_names={"steve"}, linked_accounts=(account,)
        )
    )
    assert [c.args[2] for c in updater.await_args_list] == ["claim", "cancel"]
    assert bot._rcon.delivered == []
    expected = "experience add @s 500 points" if kind == "xp" else "give @s minecraft:diamond 3"
    assert GUARD + expected in bot._rcon.commands
    assert getattr(bot._accounts, f"get_minecraft_{kind}_exchange_delivery")("exchange-7") is None


def test_market_atomic_refusal_releases_wallet_and_restores_listing(tmp_path) -> None:
    bot, account = _bot(tmp_path, "world")
    bot._market.get = Mock(return_value=SimpleNamespace(status="active"))
    bot._market.reserve_purchase = Mock(
        return_value=SimpleNamespace(
            purchase_request_id=REQUEST_ID,
            listing_id=17,
            seller_discord_user_id=321,
            seller_account_id=2,
            price_xp=100,
        )
    )
    bot._market.set_status = Mock()
    bot._refresh_market_listing = AsyncMock()
    bot._level_bot_xp.request_market_purchase.return_value = SimpleNamespace(status="reserved")
    bot._level_bot_xp.update_market_purchase = AsyncMock(return_value=True)
    original = bot._rcon.execute

    def execute(command):
        if command.startswith("usapo-event-bridge market-deliver "):
            return f"USAPO_MARKET_TRANSFER_RESULT|1|{REQUEST_ID}|17|world_restricted|active|new"
        return original(command)

    bot._rcon.execute = execute
    result = asyncio.run(
        bot._purchase_market(
            guild_id=456, listing_id=17, request_id=REQUEST_ID, expected_price_xp=100, buyer=account
        )
    )
    assert WORLD_RESTRICTED_MESSAGE in result
    bot._level_bot_xp.update_market_purchase.assert_awaited_once_with(
        request_id=REQUEST_ID, guild_id=456, action="cancel"
    )
    bot._market.set_status.assert_called_once_with(17, REQUEST_ID, "active")


def test_buyback_atomic_refusal_cancels_and_releases_pending_items(tmp_path) -> None:
    bot, account = _bot(tmp_path)
    request = MinecraftExchangeRequest(
        REQUEST_ID,
        PLAYER_UUID,
        "Steve",
        "material_buyback",
        "minecraft:emerald",
        64,
        0,
        500,
        datetime.now(UTC).isoformat(),
    )
    bot._level_bot_xp.request_material_buyback = AsyncMock(
        return_value=SimpleNamespace(
            status="reserved",
            request_id=REQUEST_ID,
            item_id="minecraft:emerald",
            item_count=64,
            reward_xp=500,
            item_name="エメラルド",
        )
    )
    bot._level_bot_xp.update_material_buyback = AsyncMock(return_value=True)
    bot._release_material_buyback_request = AsyncMock()
    bot._send_minecraft_private_message = AsyncMock()
    bot._execute_rcon = AsyncMock(
        return_value=(
            f"USAPO_MATERIAL_BUYBACK_RESULT|1|{REQUEST_ID}|world_restricted|minecraft:emerald|64|new"
        )
    )
    asyncio.run(
        bot._handle_minecraft_material_buyback_request(
            request, guild_id=456, user_id=123, account_id=account.id
        )
    )
    bot._level_bot_xp.update_material_buyback.assert_awaited_once_with(
        request_id=REQUEST_ID, guild_id=456, user_id=123, action="cancel"
    )
    bot._release_material_buyback_request.assert_awaited_once_with(request)
    bot._send_minecraft_private_message.assert_awaited_once_with("Steve", WORLD_RESTRICTED_MESSAGE)


@pytest.mark.parametrize("method", ["cancel_quest", "show_quest_action_confirmation"])
def test_existing_quest_buttons_deny_current_restricted_world(tmp_path, method) -> None:
    bot, _ = _bot(tmp_path)
    bot._quests.get = Mock(
        return_value=SimpleNamespace(
            status="open", is_system_issued=False, owner_discord_user_id=123
        )
    )
    bot._run_quest_action = AsyncMock()
    interaction = _interaction()
    args = (17, "cancel") if method == "show_quest_action_confirmation" else (17,)
    asyncio.run(getattr(bot, method)(interaction, *args))
    interaction.followup.send.assert_awaited_once_with(WORLD_RESTRICTED_MESSAGE, ephemeral=True)
    bot._run_quest_action.assert_not_awaited()


def test_slow_world_lookup_returns_friendly_button_denial(tmp_path) -> None:
    bot, _ = _bot(tmp_path)
    bot._discord_economy_error = AsyncMock(side_effect=TimeoutError())
    interaction = _interaction()
    assert asyncio.run(bot._deny_economy_interaction(interaction)) is True
    interaction.response.send_message.assert_awaited_once_with(
        ECONOMY_UNAVAILABLE_MESSAGE, ephemeral=True
    )


@pytest.mark.parametrize(
    ("initial_world", "action_world", "expected_status"),
    [
        ("world", "resource", "world_restricted"),
        ("world", "world_2_nether", "world_restricted"),
        ("world", "world_2_the_end", "world_restricted"),
        ("resource", "resource", "preflight_denied"),
        ("world", "world", "completed"),
        ("offline", "offline", "completed"),
    ],
)
def test_user_quest_abandon_uses_checked_endpoint_and_preserves_offline_behavior(
    tmp_path, initial_world, action_world, expected_status
) -> None:
    bot, _ = _bot(tmp_path, initial_world)
    interaction = _interaction()
    commands: list[str] = []
    original_execute = bot._rcon.execute

    def execute(command: str) -> str:
        if command.startswith("usapo-event-bridge quest-"):
            fields = command.split()
            assert fields[:4] == ["usapo-event-bridge", "quest-user-abandon", "17", PLAYER_UUID]
            UUID(fields[4])
            commands.append(command)
            # Paper rechecks on the mutation tick, independently of the earlier
            # economy-access result. Offline non-inventory actions stay allowed.
            status = (
                "world_restricted"
                if bot._rcon.world in {"resource", "world_2_nether", "world_2_the_end"}
                else "completed"
            )
            quest_status = "accepted" if status == "world_restricted" else "open"
            return f"USAPO_QUEST_ACTION_RESULT|1|{fields[4]}|17|{status}|{quest_status}|new"
        return original_execute(command)

    def load_quest(quest_id: int):
        assert quest_id == 17
        bot._rcon.world = action_world
        return SimpleNamespace(
            quest_id=17,
            status="accepted",
            worker_discord_user_id=123,
            worker_uuid=PLAYER_UUID,
        )

    bot._rcon.execute = execute
    bot._quests.get = Mock(side_effect=load_quest)
    asyncio.run(bot.abandon_quest(interaction, 17))

    if expected_status == "preflight_denied":
        assert commands == []
        bot._quests.get.assert_not_called()
        interaction.response.send_message.assert_awaited_once_with(
            WORLD_RESTRICTED_MESSAGE, ephemeral=True
        )
    else:
        assert len(commands) == 1
        expected_message = (
            WORLD_RESTRICTED_MESSAGE
            if expected_status == "world_restricted"
            else "クエストを辞退しました。依頼は掲示板で再募集されます。"
        )
        interaction.followup.send.assert_awaited_once_with(expected_message, ephemeral=True)
