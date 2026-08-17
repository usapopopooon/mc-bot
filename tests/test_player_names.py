import pytest

from mc_bot.player_names import is_safe_server_player_name


@pytest.mark.parametrize(
    "player_name",
    [
        "Steve",
        ".Bedrock_User",
        "*Bedrock_User",
        "+Bedrock_User",
        "-Bedrock_User",
    ],
)
def test_accepts_java_and_safe_floodgate_server_names(player_name: str) -> None:
    assert is_safe_server_player_name(player_name)


@pytest.mark.parametrize(
    "player_name",
    [
        "",
        "@a",
        "Steve run kill @a",
        'Steve"',
        "a" * 34,
    ],
)
def test_rejects_names_that_can_change_an_rcon_argument(player_name: str) -> None:
    assert not is_safe_server_player_name(player_name)
