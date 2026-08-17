from __future__ import annotations

import re

# Java名に加え、Floodgateが先頭へ付ける安全な記号を許可する。
# 空白、引用符、セレクターの「@」などRCON引数の意味を変える文字は許可しない。
_SAFE_SERVER_PLAYER_NAME = re.compile(r"[A-Za-z0-9_.*+-]{1,33}")


def is_safe_server_player_name(player_name: str) -> bool:
    return _SAFE_SERVER_PLAYER_NAME.fullmatch(player_name) is not None


__all__ = ["is_safe_server_player_name"]
