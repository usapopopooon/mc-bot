from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto


class EventType(Enum):
    CHAT = auto()
    ADVANCEMENT = auto()
    JOIN = auto()
    LEAVE = auto()


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
    return None
