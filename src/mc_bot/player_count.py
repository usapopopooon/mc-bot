from __future__ import annotations

import re

_ONLINE_COUNT = re.compile(
    r"\bThere are\s+(\d+)\s+of a max of\s+\d+\s+players online\b",
    re.IGNORECASE,
)
PLAYER_COUNT_DISABLED_CHANNEL_NAME = "⚫マイクラ 人数表示停止"


def parse_online_player_count(response: str) -> int:
    match = _ONLINE_COUNT.search(response)
    if match is None:
        raise ValueError("Minecraftのオンライン人数を読み取れませんでした")
    return int(match.group(1))


def player_count_channel_name(count: int | None) -> str:
    if count is None:
        return "🔴マイクラ停止中"
    if count == 0:
        return "⚪マイクラ 0人"
    return f"🟢マイクラ {count}人"
