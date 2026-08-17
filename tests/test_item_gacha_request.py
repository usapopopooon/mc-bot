import pytest

from mc_bot.item_gacha_request import MinecraftItemGachaRequest, parse_item_gacha_request


def test_parses_uuid_based_game_command_request() -> None:
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        "USAPO_ITEM_GACHA_REQUEST|2|11111111-1111-4111-8111-111111111111|"
        "22222222-2222-4222-8222-222222222222|Lll1a2kxOTkx|premium|1000|1786838400000"
    )

    assert parse_item_gacha_request(line) == MinecraftItemGachaRequest(
        request_id="11111111-1111-4111-8111-111111111111",
        player_uuid="22222222-2222-4222-8222-222222222222",
        player_name=".Yuki1991",
        draw_kind="premium",
        expected_cost_xp=1_000,
        requested_at="2026-08-16T00:00:00+00:00",
    )


def test_parses_legacy_request_without_trusting_an_unconfirmed_price() -> None:
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        "USAPO_ITEM_GACHA_REQUEST|1|11111111-1111-4111-8111-111111111111|"
        "22222222-2222-4222-8222-222222222222|U3RldmU|normal|1786838400000"
    )

    request = parse_item_gacha_request(line)

    assert request is not None
    assert request.expected_cost_xp is None


def test_accepts_safe_star_floodgate_prefix() -> None:
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] "
        "USAPO_ITEM_GACHA_REQUEST|2|11111111-1111-4111-8111-111111111111|"
        "22222222-2222-4222-8222-222222222222|KlN0ZXZl|normal|100|1786838400000"
    )

    request = parse_item_gacha_request(line)

    assert request is not None
    assert request.player_name == "*Steve"


def test_ignores_unrelated_log_line() -> None:
    assert (
        parse_item_gacha_request("[08:24:00] [Server thread/INFO]: Steve joined the game") is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        "bad|22222222-2222-4222-8222-222222222222|U3RldmU|normal|100|1786838400000",
        "11111111-1111-4111-8111-111111111111|bad|U3RldmU|normal|100|1786838400000",
        "11111111-1111-4111-8111-111111111111|22222222-2222-4222-8222-222222222222|"
        "***|normal|100|1786838400000",
        "11111111-1111-4111-8111-111111111111|22222222-2222-4222-8222-222222222222|"
        "U3RldmU|mythic|100|1786838400000",
        "11111111-1111-4111-8111-111111111111|22222222-2222-4222-8222-222222222222|"
        "U3RldmU|normal|0|1786838400000",
        "11111111-1111-4111-8111-111111111111|22222222-2222-4222-8222-222222222222|"
        "U3RldmU|normal|100|-1",
    ],
)
def test_rejects_malformed_game_command_request(payload: str) -> None:
    line = (
        f"[08:24:00] [Server thread/INFO]: [UsapoEventBridge] USAPO_ITEM_GACHA_REQUEST|2|{payload}"
    )

    with pytest.raises(ValueError, match="Minecraft item gacha request"):
        parse_item_gacha_request(line)
