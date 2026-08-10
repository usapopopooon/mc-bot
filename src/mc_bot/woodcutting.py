from __future__ import annotations

import json
import re

WOODCUTTING_COMBO_WINDOW_SECONDS = 30

_SAFE_PLAYER_NAME = re.compile(r"\.?[A-Za-z0-9_]{1,32}")


def woodcutting_reward_xp(combo_count: int) -> int:
    if combo_count == 5:
        return 5
    if combo_count == 10:
        return 15
    if combo_count >= 20 and combo_count % 10 == 0:
        return 30
    return 0


def is_public_woodcutting_milestone(combo_count: int) -> bool:
    return combo_count == 20 or (combo_count >= 50 and combo_count % 50 == 0)


def woodcutting_actionbar_command(player_name: str, combo_count: int, reward_xp: int) -> str:
    _validate_player_name(player_name)
    if combo_count < 1 or reward_xp <= 0:
        raise ValueError("rewarded woodcutting combo is invalid")
    component = {
        "text": f"🪓 連続伐採{combo_count}本! +{reward_xp} XP",
        "color": "green",
        "bold": True,
    }
    return (
        f"title {player_name} actionbar "
        f"{json.dumps(component, ensure_ascii=False, separators=(',', ':'))}"
    )


def woodcutting_tellraw_command(
    player_name: str,
    combo_count: int,
    reward_xp: int,
) -> str:
    _validate_player_name(player_name)
    if not is_public_woodcutting_milestone(combo_count) or reward_xp <= 0:
        raise ValueError("public woodcutting milestone is invalid")
    components = [
        {"text": "🪓 "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんが連続伐採"},
        {"text": f"{combo_count}本", "color": "green", "bold": True},
        {"text": "を達成! "},
        {"text": f"+{reward_xp} XP", "color": "green", "bold": True},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def woodcutting_xp_sound_command(player_name: str) -> str:
    _validate_player_name(player_name)
    return f"playsound minecraft:entity.experience_orb.pickup player {player_name} ~ ~ ~ 1 1"


def _validate_player_name(player_name: str) -> None:
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
