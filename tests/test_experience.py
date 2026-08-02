import pytest

from mc_bot.experience import (
    experience_query_command,
    experience_to_next_level,
    parse_experience_query,
    total_experience_points,
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
