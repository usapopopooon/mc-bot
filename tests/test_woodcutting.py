import pytest

from mc_bot.woodcutting import (
    WOODCUTTING_COMBO_WINDOW_SECONDS,
    is_public_woodcutting_milestone,
    woodcutting_actionbar_command,
    woodcutting_reward_xp,
    woodcutting_tellraw_command,
    woodcutting_xp_sound_command,
)


@pytest.mark.parametrize(
    ("combo_count", "expected"),
    [(0, 0), (4, 0), (5, 5), (6, 0), (10, 15), (19, 0), (20, 30), (30, 30), (31, 0)],
)
def test_woodcutting_reward_milestones(combo_count: int, expected: int) -> None:
    assert woodcutting_reward_xp(combo_count) == expected


def test_woodcutting_combo_window_is_always_thirty_seconds() -> None:
    assert WOODCUTTING_COMBO_WINDOW_SECONDS == 30


def test_builds_private_feedback_with_experience_pickup_sound() -> None:
    assert woodcutting_actionbar_command("Steve", 10, 15).startswith(
        'title Steve actionbar {"text":"🪓 連続伐採10本! +15 XP"'
    )


@pytest.mark.parametrize(
    ("combo_count", "expected"),
    [(19, False), (20, True), (40, False), (50, True), (90, False), (100, True)],
)
def test_public_woodcutting_milestones(combo_count: int, expected: bool) -> None:
    assert is_public_woodcutting_milestone(combo_count) is expected


def test_builds_public_woodcutting_milestone() -> None:
    command = woodcutting_tellraw_command("Steve", 20, 30)
    assert command.startswith('tellraw @a [{"text":"🪓 "},{"text":"Steve"')
    assert "連続伐採" in command
    assert "20本" in command
    assert "+30 XP" in command
    assert woodcutting_xp_sound_command("Steve") == (
        "playsound minecraft:entity.experience_orb.pickup player Steve ~ ~ ~ 1 1"
    )


@pytest.mark.parametrize("player_name", ["@a", "Steve run kill @a", ""])
def test_rejects_unsafe_woodcutting_player_names(player_name: str) -> None:
    with pytest.raises(ValueError):
        woodcutting_actionbar_command(player_name, 5, 2)
    with pytest.raises(ValueError):
        woodcutting_tellraw_command(player_name, 20, 10)
    with pytest.raises(ValueError):
        woodcutting_xp_sound_command(player_name)
