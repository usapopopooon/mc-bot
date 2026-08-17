import pytest

from mc_bot.exchange_request import MinecraftExchangeRequest, parse_exchange_request


def test_parses_confirmed_resource_exchange_request() -> None:
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        "USAPO_EXCHANGE_REQUEST|1|11111111-1111-4111-8111-111111111111|"
        "22222222-2222-4222-8222-222222222222|Lll1a2kxOTkx|resource|"
        "minecraft:diamond|3|2160|3|1786924800000"
    )

    assert parse_exchange_request(line) == MinecraftExchangeRequest(
        request_id="11111111-1111-4111-8111-111111111111",
        player_uuid="22222222-2222-4222-8222-222222222222",
        player_name=".Yuki1991",
        kind="resource",
        target="minecraft:diamond",
        amount=3,
        expected_cost_xp=2_160,
        expected_reward=3,
        requested_at="2026-08-17T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    "selection",
    [
        "balance|balance|0|0|0",
        "xp|minecraft:experience|500|100|500",
        "resource|minecraft:emerald|16|360|16",
        "emerald_diamond|minecraft:diamond|64|0|2",
    ],
)
def test_accepts_each_exchange_kind(selection: str) -> None:
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        "USAPO_EXCHANGE_REQUEST|1|11111111-1111-4111-8111-111111111111|"
        "22222222-2222-4222-8222-222222222222|U3RldmU|"
        f"{selection}|1786924800000"
    )

    assert parse_exchange_request(line) is not None


def test_accepts_safe_star_floodgate_prefix() -> None:
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        "USAPO_EXCHANGE_REQUEST|1|11111111-1111-4111-8111-111111111111|"
        "22222222-2222-4222-8222-222222222222|KlN0ZXZl|balance|balance|0|0|0|"
        "1786924800000"
    )

    request = parse_exchange_request(line)

    assert request is not None
    assert request.player_name == "*Steve"


@pytest.mark.parametrize(
    "selection",
    [
        "balance|balance|1|0|0",
        "xp|minecraft:experience|500|100|250",
        "xp|minecraft:diamond|500|100|500",
        "resource|minecraft:netherite_ingot|1|100|1",
        "resource|minecraft:diamond|65|100|65",
        "emerald_diamond|minecraft:diamond|16|0|1",
        "emerald_diamond|minecraft:diamond|32|100|1",
        "unknown|balance|0|0|0",
    ],
)
def test_rejects_tampered_exchange_selection(selection: str) -> None:
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        "USAPO_EXCHANGE_REQUEST|1|11111111-1111-4111-8111-111111111111|"
        "22222222-2222-4222-8222-222222222222|U3RldmU|"
        f"{selection}|1786924800000"
    )

    with pytest.raises(ValueError, match="Minecraft exchange request"):
        parse_exchange_request(line)
