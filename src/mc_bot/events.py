from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

from mc_bot.deaths import is_death_detail


class EventType(Enum):
    CHAT = auto()
    ADVANCEMENT = auto()
    JOIN = auto()
    LEAVE = auto()
    DEATH = auto()


@dataclass(frozen=True, slots=True)
class LogEvent:
    type: EventType
    player_name: str
    detail: str = ""


_CHAT = re.compile(r"^(?:\[Not Secure] )?<([^>]+)> (.+)$")
_JOIN = re.compile(r"^(.+?) joined the game$")
_LEAVE = re.compile(r"^(.+?) left the game$")
_ADVANCEMENT = re.compile(
    r"^(.+?) (?:has made the advancement|has completed the challenge|has reached the goal) "
    r"\[(.+)]$"
)
_PLAYER_EVENT = re.compile(r"^(\.?[A-Za-z0-9_]{1,32}) (.+)$")


def parse_log_line(line: str) -> LogEvent | None:
    _, separator, message = line.partition("]: ")
    if not separator:
        return None

    if match := _CHAT.fullmatch(message):
        return LogEvent(EventType.CHAT, match[1], match[2])
    if match := _ADVANCEMENT.fullmatch(message):
        return LogEvent(EventType.ADVANCEMENT, match[1], match[2])
    if match := _JOIN.fullmatch(message):
        return LogEvent(EventType.JOIN, match[1])
    if match := _LEAVE.fullmatch(message):
        return LogEvent(EventType.LEAVE, match[1])
    if (match := _PLAYER_EVENT.fullmatch(message)) and is_death_detail(match[2]):
        return LogEvent(EventType.DEATH, match[1], match[2])
    return None
