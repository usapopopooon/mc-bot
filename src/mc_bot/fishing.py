from __future__ import annotations

import json

from mc_bot.player_names import is_safe_server_player_name

FISHING_COMBO_WINDOW_SECONDS = 90


def fishing_reward_xp(combo_count: int) -> int:
    if combo_count < 1:
        return 0
    if combo_count < 2:
        return 2
    if combo_count < 3:
        return 5
    if combo_count < 5:
        return 7
    if combo_count < 10:
        return 10
    if combo_count < 20:
        return 15
    return 20


def is_public_fishing_milestone(combo_count: int) -> bool:
    return combo_count >= 10 and combo_count % 10 == 0


def fishing_combo_actionbar_command(
    player_name: str,
    combo_count: int,
    reward_xp: int,
) -> str:
    _validate_player_name(player_name)
    if combo_count < 1 or reward_xp <= 0:
        raise ValueError("rewarded fishing combo is invalid")
    text = (
        f"🎣 釣りボーナス! +{reward_xp} XP"
        if combo_count == 1
        else f"🎣 連続釣り{combo_count}回! +{reward_xp} XP"
    )
    component = {
        "text": text,
        "color": "aqua",
        "bold": True,
    }
    return (
        f"title {player_name} actionbar "
        f"{json.dumps(component, ensure_ascii=False, separators=(',', ':'))}"
    )


def fishing_combo_tellraw_command(
    player_name: str,
    combo_count: int,
    reward_xp: int,
) -> str:
    _validate_player_name(player_name)
    if not is_public_fishing_milestone(combo_count) or reward_xp <= 0:
        raise ValueError("public fishing milestone is invalid")
    components = [
        {"text": "🎣 "},
        {"text": player_name, "color": "yellow"},
        {"text": "さんが釣り"},
        {"text": f"{combo_count}コンボ", "color": "aqua", "bold": True},
        {"text": "を達成! "},
        {"text": f"+{reward_xp} XP", "color": "green", "bold": True},
    ]
    return f"tellraw @a {json.dumps(components, ensure_ascii=False, separators=(',', ':'))}"


def _validate_player_name(player_name: str) -> None:
    if not is_safe_server_player_name(player_name):
        raise ValueError("player_name contains unsafe RCON characters")
