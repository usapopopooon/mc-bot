from __future__ import annotations

import json
import re

FISHING_OBJECTIVE = "mc_fish_caught"
FISHING_COMBO_WINDOW_SECONDS = 90

_SAFE_PLAYER_NAME = re.compile(r"\.?[A-Za-z0-9_]{1,32}")
_SCORE = re.compile(
    rf"\bhas\s+(\d+)\s+\[{re.escape(FISHING_OBJECTIVE)}]",
    re.IGNORECASE,
)


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


def fishing_objective_command() -> str:
    return f"scoreboard objectives add {FISHING_OBJECTIVE} minecraft.custom:minecraft.fish_caught"


def fishing_score_query_command(player_name: str) -> str:
    _validate_player_name(player_name)
    return f"scoreboard players get {player_name} {FISHING_OBJECTIVE}"


def parse_fishing_score(response: str) -> int:
    if match := _SCORE.search(response):
        return int(match[1])
    lowered = response.casefold()
    if "none is set" in lowered or "has no score" in lowered:
        return 0
    raise ValueError("Minecraftの釣果数を読み取れませんでした")


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


def _validate_player_name(player_name: str) -> None:
    if _SAFE_PLAYER_NAME.fullmatch(player_name) is None:
        raise ValueError("player_name contains unsafe RCON characters")
