import pytest

from mc_bot.fishing import (
    fishing_combo_actionbar_command,
    fishing_combo_tellraw_command,
    fishing_reward_xp,
    is_public_fishing_milestone,
)


@pytest.mark.parametrize(
    ("combo_count", "expected"),
    [(0, 0), (1, 2), (2, 5), (3, 7), (4, 7), (5, 10), (9, 10), (10, 15), (19, 15), (20, 20)],
)
def test_fishing_reward_tiers(combo_count: int, expected: int) -> None:
    assert fishing_reward_xp(combo_count) == expected


def test_builds_private_fishing_reward_feedback() -> None:
    assert fishing_combo_actionbar_command("Steve", 5, 10).startswith(
        'title Steve actionbar {"text":"🎣 連続釣り5回! +10 XP"'
    )


@pytest.mark.parametrize(
    ("combo_count", "expected"),
    [(9, False), (10, True), (19, False), (20, True), (30, True)],
)
def test_public_fishing_milestones(combo_count: int, expected: bool) -> None:
    assert is_public_fishing_milestone(combo_count) is expected


def test_builds_public_fishing_milestone_without_sound() -> None:
    command = fishing_combo_tellraw_command("Steve", 10, 15)
    assert command.startswith('tellraw @a [{"text":"🎣 "},{"text":"Steve"')
    assert "10コンボ" in command
    assert "+15 XP" in command
    assert fishing_combo_actionbar_command("Steve", 1, 2).startswith(
        'title Steve actionbar {"text":"🎣 釣りボーナス! +2 XP"'
    )


@pytest.mark.parametrize("player_name", ["@a", "Steve run kill @a", ""])
def test_rejects_unsafe_fishing_player_names(player_name: str) -> None:
    with pytest.raises(ValueError):
        fishing_combo_tellraw_command(player_name, 10, 15)
