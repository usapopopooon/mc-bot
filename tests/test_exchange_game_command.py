import asyncio
import base64
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.experience import (
    MinecraftResourceExchangeRequest,
    MinecraftResourcePack,
    MinecraftResourceShop,
    MinecraftXpExchangeRequest,
    MinecraftXpPack,
    MinecraftXpShop,
    MinecraftXpWallet,
)
from mc_bot.settings import RuntimeSettings
from mc_bot.tailer import Cursor, PendingLine

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


class ExchangeRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("tellraw "):
            return ""
        if command.startswith("usapo-event-bridge emerald-diamond-v2 "):
            request_id = command.rsplit(" ", 1)[1]
            return f"USAPO_EMERALD_EXCHANGE_RESULT|2|{request_id}|completed|64|2|new"
        raise AssertionError(f"unexpected RCON command: {command}")


def _line(
    offset: int,
    *,
    request_id: str,
    selection: str,
    player_name: str = "Steve",
    requested_at_ms: int | None = None,
) -> PendingLine:
    if requested_at_ms is None:
        requested_at_ms = int(datetime.now(UTC).timestamp() * 1_000)
    encoded_name = base64.urlsafe_b64encode(player_name.encode()).decode().rstrip("=")
    return PendingLine(
        text=(
            "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
            f"USAPO_EXCHANGE_REQUEST|1|{request_id}|{PLAYER_UUID}|{encoded_name}|"
            f"{selection}|{requested_at_ms}"
        ),
        cursor=Cursor("log-1", offset),
    )


def _bot(tmp_path, *, linked: bool = True) -> MinecraftDiscordBot:
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
    if linked:
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
    bot._settings = RuntimeSettings(guild_id=456)
    bot._rcon = ExchangeRcon()  # type: ignore[assignment]
    wallet = MinecraftXpWallet(total_xp=50_000, spent_xp=1_000, available_xp=49_000)
    bot._level_bot_xp.fetch_xp_shop = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftXpShop(
            wallet,
            (
                MinecraftXpPack(10, 50),
                MinecraftXpPack(50, 250),
                MinecraftXpPack(100, 500),
                MinecraftXpPack(1_000, 5_000),
            ),
        )
    )
    bot._level_bot_xp.request_xp_exchange = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftXpExchangeRequest(
            status="reserved",
            message="Minecraft 500 XPへの交換を受け付けました。",
            wallet_before=wallet,
            wallet_after=MinecraftXpWallet(50_000, 1_100, 48_900),
            pack=MinecraftXpPack(100, 500),
        )
    )
    bot._level_bot_xp.fetch_resource_shop = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftResourceShop(
            wallet,
            (MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 3, 2_160),),
        )
    )
    bot._level_bot_xp.request_resource_exchange = AsyncMock(  # type: ignore[method-assign]
        return_value=MinecraftResourceExchangeRequest(
            status="reserved",
            message="ダイヤモンド x3への交換を受け付けました。",
            wallet_before=wallet,
            wallet_after=MinecraftXpWallet(50_000, 3_160, 46_840),
            pack=MinecraftResourcePack("minecraft:diamond", "ダイヤモンド", 3, 2_160),
        )
    )
    return bot


def test_game_exchange_wires_every_selection_to_the_existing_authority(tmp_path) -> None:
    bot = _bot(tmp_path)
    lines = [
        _line(
            100,
            request_id="11111111-1111-4111-8111-111111111111",
            selection="balance|balance|0|0|0",
        ),
        _line(
            200,
            request_id="22222222-2222-4222-8222-222222222222",
            selection="xp|minecraft:experience|500|100|500",
        ),
        _line(
            300,
            request_id="33333333-3333-4333-8333-333333333333",
            selection="resource|minecraft:diamond|3|2160|3",
        ),
        _line(
            400,
            request_id="44444444-4444-4444-8444-444444444444",
            selection="emerald_diamond|minecraft:diamond|64|0|2",
        ),
    ]
    tailer = LineTailer(lines)
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == lines
    bot._level_bot_xp.fetch_xp_shop.assert_any_await(456, 123)  # type: ignore[attr-defined]
    bot._level_bot_xp.request_xp_exchange.assert_awaited_once_with(  # type: ignore[attr-defined]
        456,
        123,
        "22222222-2222-4222-8222-222222222222",
        100,
        500,
    )
    bot._level_bot_xp.fetch_resource_shop.assert_awaited_once_with(456, 123)  # type: ignore[attr-defined]
    bot._level_bot_xp.request_resource_exchange.assert_awaited_once_with(  # type: ignore[attr-defined]
        456,
        123,
        "33333333-3333-4333-8333-333333333333",
        "minecraft:diamond",
        3,
        2_160,
    )
    rcon = bot._rcon
    assert isinstance(rcon, ExchangeRcon)
    assert (
        "usapo-event-bridge emerald-diamond-v2 "
        f"{PLAYER_UUID} 64 44444444-4444-4444-8444-444444444444"
    ) in rcon.commands
    private = [command for command in rcon.commands if command.startswith("tellraw Steve ")]
    assert len(private) == 4
    assert "現在XP: 49,000 XP" in private[0]
    assert "Minecraft 500 XPへの交換" in private[1]
    assert "ダイヤモンド x3への交換" in private[2]
    assert "交換完了: エメラルド x64" in private[3]


def test_changed_price_is_rejected_before_any_xp_spend(tmp_path) -> None:
    bot = _bot(tmp_path)
    line = _line(
        100,
        request_id="11111111-1111-4111-8111-111111111111",
        selection="xp|minecraft:experience|500|99|500",
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    bot._level_bot_xp.request_xp_exchange.assert_not_awaited()  # type: ignore[attr-defined]
    rcon = bot._rcon
    assert isinstance(rcon, ExchangeRcon)
    assert len(rcon.commands) == 1
    assert "価格が更新" in rcon.commands[0]


def test_changed_resource_price_is_rejected_before_any_xp_spend(tmp_path) -> None:
    bot = _bot(tmp_path)
    line = _line(
        100,
        request_id="11111111-1111-4111-8111-111111111111",
        selection="resource|minecraft:diamond|3|2159|3",
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    bot._level_bot_xp.request_resource_exchange.assert_not_awaited()  # type: ignore[attr-defined]
    rcon = bot._rcon
    assert isinstance(rcon, ExchangeRcon)
    assert len(rcon.commands) == 1
    assert "価格が更新" in rcon.commands[0]


def test_unlinked_game_exchange_never_reaches_level_bot(tmp_path) -> None:
    bot = _bot(tmp_path, linked=False)
    line = _line(
        100,
        request_id="11111111-1111-4111-8111-111111111111",
        selection="resource|minecraft:diamond|3|2160|3",
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    bot._level_bot_xp.fetch_resource_shop.assert_not_awaited()  # type: ignore[attr-defined]
    bot._level_bot_xp.request_resource_exchange.assert_not_awaited()  # type: ignore[attr-defined]
    rcon = bot._rcon
    assert isinstance(rcon, ExchangeRcon)
    assert len(rcon.commands) == 1
    assert "Discordアカウントとの連携" in rcon.commands[0]


def test_stale_game_exchange_never_reaches_level_bot(tmp_path) -> None:
    bot = _bot(tmp_path)
    line = _line(
        100,
        request_id="11111111-1111-4111-8111-111111111111",
        selection="xp|minecraft:experience|500|100|500",
        requested_at_ms=int(datetime.now(UTC).timestamp() * 1_000) - 600_000,
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    assert tailer.acknowledged == [line]
    bot._level_bot_xp.fetch_xp_shop.assert_not_awaited()  # type: ignore[attr-defined]
    bot._level_bot_xp.request_xp_exchange.assert_not_awaited()  # type: ignore[attr-defined]
    rcon = bot._rcon
    assert isinstance(rcon, ExchangeRcon)
    assert len(rcon.commands) == 1
    assert "期限切れ" in rcon.commands[0]


def test_game_exchange_updates_current_name_by_uuid_before_async_delivery(tmp_path) -> None:
    bot = _bot(tmp_path)
    line = _line(
        100,
        request_id="11111111-1111-4111-8111-111111111111",
        selection="xp|minecraft:experience|500|100|500",
        player_name="Alex",
    )
    tailer = LineTailer([line])
    bot._tailer = tailer  # type: ignore[assignment]

    asyncio.run(bot._forward_logs())

    account = bot._accounts.get_by_player_uuid(PLAYER_UUID)
    assert account is not None
    assert account.minecraft_name == "Alex"
    assert account.server_player_name == "Alex"
    bot._level_bot_xp.request_xp_exchange.assert_awaited_once()  # type: ignore[attr-defined]
    rcon = bot._rcon
    assert isinstance(rcon, ExchangeRcon)
    assert len(rcon.commands) == 1
    assert rcon.commands[0].startswith("tellraw Alex ")
