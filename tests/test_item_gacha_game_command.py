import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import MinecraftItemGachaSpendRequest, MinecraftXpWallet
from mc_bot.item_gacha import get_item_gacha_reward, item_gacha_day
from mc_bot.settings import RuntimeSettings
from mc_bot.tailer import Cursor, PendingLine

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
PLAYER_UUID = "22222222-2222-4222-8222-222222222222"


class LineTailer:
    def __init__(self, lines: list[PendingLine]) -> None:
        self.pending = lines
        self.acknowledged: list[PendingLine] = []

    async def lines(self):  # type: ignore[no-untyped-def]
        for line in self.pending:
            yield line

    def acknowledge(self, line: PendingLine) -> None:
        self.acknowledged.append(line)


class GameCommandRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("give Steve "):
            return "Gave 3 [Diamond] to Steve"
        if command.startswith("tellraw @a ") or command.startswith("tellraw Steve "):
            return ""
        raise AssertionError(f"unexpected RCON command: {command}")


def _request_line(
    offset: int,
    *,
    request_id: str = REQUEST_ID,
    draw_kind: str = "premium",
    expected_cost_xp: int | None = None,
    protocol_version: int = 2,
    requested_at_ms: int | None = None,
) -> PendingLine:
    if requested_at_ms is None:
        requested_at_ms = int(datetime.now(UTC).timestamp() * 1_000)
    if expected_cost_xp is None:
        expected_cost_xp = 1_000 if draw_kind == "premium" else 100
    selection = f"{draw_kind}|{expected_cost_xp}" if protocol_version == 2 else draw_kind
    return PendingLine(
        text=(
            "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
            f"USAPO_ITEM_GACHA_REQUEST|{protocol_version}|{request_id}|{PLAYER_UUID}|"
            f"U3RldmU|{selection}|{requested_at_ms}"
        ),
        cursor=Cursor("log-1", offset),
    )


def _bot(tmp_path) -> MinecraftDiscordBot:
    bot = MinecraftDiscordBot(
        Config(
            discord_token="test",
            accounts_path=tmp_path / "accounts.db",
            rcon_password="test",
            level_bot_api_url="http://level-bot",
            level_bot_api_token="secret",
        )
    )
    bot._accounts.initialize()
    bot._settings = RuntimeSettings(guild_id=456)
    bot._rcon = GameCommandRcon()  # type: ignore[assignment]
    bot._channel = SimpleNamespace(send=AsyncMock())  # type: ignore[assignment]
    wallet_before = MinecraftXpWallet(total_xp=2_000, spent_xp=0, available_xp=2_000)
    wallet_after = MinecraftXpWallet(total_xp=2_000, spent_xp=1_000, available_xp=1_000)
    bot._level_bot_xp.request_item_gacha_spend = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftItemGachaSpendRequest(
            status="reserved",
            message="予約しました。",
            cost_xp=1_000,
            wallet_before=wallet_before,
            wallet_after=wallet_after,
        )
    )
    bot._level_bot_xp.update_item_gacha_spend = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    return bot


def test_game_command_uses_linked_discord_xp_and_replay_does_not_give_twice(tmp_path) -> None:
    bot = _bot(tmp_path)
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
        player_uuid=PLAYER_UUID,
    )
    lines = [_request_line(100), _request_line(200)]
    tailer = LineTailer(lines)
    bot._tailer = tailer  # type: ignore[assignment]

    with patch(
        "mc_bot.bot.draw_item_gacha_reward",
        return_value=get_item_gacha_reward("r_diamond"),
    ):
        asyncio.run(bot._forward_logs())

    rcon = bot._rcon
    assert isinstance(rcon, GameCommandRcon)
    assert rcon.commands.count("give Steve minecraft:diamond 3") == 1
    assert sum(command.startswith("tellraw @a ") for command in rcon.commands) == 1
    private = [command for command in rcon.commands if command.startswith("tellraw Steve ")]
    assert len(private) == 2
    assert "受け取りました" in private[0]
    assert "受取済み" in private[1]
    assert tailer.acknowledged == lines
    draw = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert draw is not None
    assert draw.draw_id == REQUEST_ID
    assert draw.account_id == account.id
    assert draw.draw_kind == "premium"
    assert draw.cost_xp == 1_000
    assert draw.status == "delivered"
    assert (
        bot._accounts.count_minecraft_item_gacha_draws(  # type: ignore[union-attr]
            guild_id=456,
            discord_user_id=123,
            draw_day=draw.draw_day,
        )
        == 1
    )
    bot._level_bot_xp.request_item_gacha_spend.assert_awaited_once_with(  # type: ignore[attr-defined]
        guild_id=456,
        user_id=123,
        request_id=REQUEST_ID,
        account_id=account.id,
        draw_day=draw.draw_day,
        expected_cost_xp=1_000,
    )


def test_game_command_requires_an_active_discord_link_and_is_acknowledged(tmp_path) -> None:
    bot = _bot(tmp_path)
    line = _request_line(100)
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    rcon = bot._rcon
    assert isinstance(rcon, GameCommandRcon)
    assert len(rcon.commands) == 1
    assert rcon.commands[0].startswith("tellraw Steve ")
    assert "Discordアカウントとの連携" in rcon.commands[0]
    assert tailer.acknowledged == [line]
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()  # type: ignore[attr-defined]


def test_stale_game_command_request_never_spends_xp(tmp_path) -> None:
    bot = _bot(tmp_path)
    line = _request_line(100, requested_at_ms=1_786_838_400_000)
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    rcon = bot._rcon
    assert isinstance(rcon, GameCommandRcon)
    assert len(rcon.commands) == 1
    assert "期限切れ" in rcon.commands[0]
    assert tailer.acknowledged == [line]
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("draw_kind", "expected_cost_xp", "protocol_version"),
    [
        ("normal", 999, 2),
        ("premium", 999, 2),
        ("normal", None, 1),
    ],
)
def test_game_command_never_charges_a_different_displayed_price(
    tmp_path,
    draw_kind: str,
    expected_cost_xp: int | None,
    protocol_version: int,
) -> None:
    bot = _bot(tmp_path)
    bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
        player_uuid=PLAYER_UUID,
    )
    line = _request_line(
        100,
        draw_kind=draw_kind,
        expected_cost_xp=expected_cost_xp,
        protocol_version=protocol_version,
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()  # type: ignore[attr-defined]
    rcon = bot._rcon
    assert isinstance(rcon, GameCommandRcon)
    assert len(rcon.commands) == 1
    assert "料金が更新" in rcon.commands[0]
    assert "XPを使っていません" in rcon.commands[0]


@pytest.mark.parametrize(
    (
        "pending_kind",
        "pending_cost",
        "reward_key",
        "requested_kind",
        "expected_label",
        "expected_argument",
    ),
    [
        ("premium", 1_000, "r_diamond", "normal", "R以上確定", "rare"),
        ("normal", 100, "n_iron", "premium", "通常", "normal"),
    ],
)
def test_game_command_never_substitutes_a_different_pending_price(
    tmp_path,
    pending_kind: str,
    pending_cost: int,
    reward_key: str,
    requested_kind: str,
    expected_label: str,
    expected_argument: str,
) -> None:
    bot = _bot(tmp_path)
    account = bot._accounts.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
        player_uuid=PLAYER_UUID,
    )
    reward = get_item_gacha_reward(reward_key)
    pending, created = bot._accounts.reserve_minecraft_item_gacha_draw(
        draw_id="33333333-3333-4333-8333-333333333333",
        guild_id=456,
        discord_user_id=123,
        account_id=account.id,
        player_name="Steve",
        draw_day=item_gacha_day(datetime.now(UTC)),
        draw_kind=pending_kind,
        cost_xp=pending_cost,
        tier=reward.tier,
        reward_key=reward.key,
        item_spec=reward.item_spec,
        item_name=reward.item_name,
        item_count=reward.item_count,
    )
    assert created
    bot._accounts.mark_minecraft_item_gacha_status(pending.draw_id, "retryable")
    line = _request_line(
        100,
        request_id="44444444-4444-4444-8444-444444444444",
        draw_kind=requested_kind,
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    rcon = bot._rcon
    assert isinstance(rcon, GameCommandRcon)
    assert len(rcon.commands) == 1
    assert rcon.commands[0].startswith("tellraw Steve ")
    assert f"{expected_label}ガチャ ({pending_cost:,} XP)" in rcon.commands[0]
    assert f"/gacha {expected_argument}" in rcon.commands[0]
    assert "今回はXPを使っていません" in rcon.commands[0]
    assert tailer.acknowledged == [line]
    saved = bot._accounts.get_minecraft_item_gacha_draw(
        guild_id=456,
        discord_user_id=123,
        draw_day=item_gacha_day(datetime.now(UTC)),
    )
    assert saved is not None
    assert saved.draw_id == pending.draw_id
    assert saved.status == "retryable"
    assert saved.draw_kind == pending_kind
    assert saved.cost_xp == pending_cost
    bot._level_bot_xp.request_item_gacha_spend.assert_not_awaited()  # type: ignore[attr-defined]
