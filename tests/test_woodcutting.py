import pytest

from mc_bot.woodcutting import (
    parse_log_break_count,
    woodcutting_actionbar_command,
    woodcutting_objective_commands,
    woodcutting_reward_xp,
    woodcutting_scores_query_command,
    woodcutting_xp_sound_command,
)


@pytest.mark.parametrize(
    ("combo_count", "expected"),
    [(0, 0), (4, 0), (5, 2), (6, 0), (10, 5), (19, 0), (20, 10), (30, 10), (31, 0)],
)
def test_woodcutting_reward_milestones(combo_count: int, expected: int) -> None:
    assert woodcutting_reward_xp(combo_count) == expected


def test_builds_and_sums_log_scoreboard_objectives() -> None:
    commands = woodcutting_objective_commands()
    assert len(commands) == 22
    assert "scoreboard objectives add wc_oak minecraft.mined:minecraft:oak_log" in commands
    assert (
        "scoreboard objectives add wcs_cherry minecraft.mined:minecraft:stripped_cherry_log"
        in commands
    )
    assert "scoreboard objectives add wc_warped minecraft.mined:minecraft:warped_stem" in commands
    assert woodcutting_scores_query_command("Steve") == "scoreboard players list Steve"
    assert (
        parse_log_break_count(
            "Player Steve has 4 scores: [wc_oak]: 3, wcs_cherry: 2, "
            "[wc_warped]: 4, [unrelated]: 999"
        )
        == 9
    )
    assert parse_log_break_count("Player Steve has no scores") == 0


def test_builds_private_feedback_with_experience_pickup_sound() -> None:
    assert woodcutting_actionbar_command("Steve", 10, 5).startswith(
        'title Steve actionbar {"text":"🪓 連続伐採10本! +5 XP"'
    )
    assert woodcutting_xp_sound_command("Steve") == (
        "playsound minecraft:entity.experience_orb.pickup player Steve ~ ~ ~ 1 1"
    )


@pytest.mark.parametrize("player_name", ["@a", "Steve run kill @a", ""])
def test_rejects_unsafe_woodcutting_player_names(player_name: str) -> None:
    with pytest.raises(ValueError):
        woodcutting_actionbar_command(player_name, 5, 2)
    with pytest.raises(ValueError):
        woodcutting_scores_query_command(player_name)
    with pytest.raises(ValueError):
        woodcutting_xp_sound_command(player_name)
