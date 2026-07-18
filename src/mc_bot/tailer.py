from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_POLL_INTERVAL_SECONDS = 0.5
_MAX_READ_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Cursor:
    file_identity: str
    offset: int


@dataclass(frozen=True, slots=True)
class PendingLine:
    text: str
    cursor: Cursor


class LogTailer:
    def __init__(self, log_path: Path, cursor_path: Path) -> None:
        self._log_path = log_path
        self._cursor_path = cursor_path
        self._cursor: Cursor | None = None

    async def lines(self) -> AsyncIterator[PendingLine]:
        while True:
            for line in await asyncio.to_thread(self.poll):
                yield line
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def validate(self) -> None:
        if not self._log_path.is_file():
            raise FileNotFoundError(f"Minecraft log does not exist: {self._log_path}")
        with self._log_path.open("rb"):
            pass
        self._cursor_path.parent.mkdir(parents=True, exist_ok=True)

    def poll(self) -> list[PendingLine]:
        try:
            stat = self._log_path.stat()
        except FileNotFoundError:
            return []
        if not self._log_path.is_file():
            return []

        identity = f"{stat.st_dev}:{stat.st_ino}"
        if self._cursor is None:
            self._cursor = self._initial_cursor(identity, stat.st_size)
            self._save_cursor(self._cursor)
            if self._cursor.offset >= stat.st_size:
                return []

        if self._cursor.file_identity != identity or stat.st_size < self._cursor.offset:
            self._cursor = Cursor(identity, 0)
            self._save_cursor(self._cursor)

        with self._log_path.open("rb") as stream:
            stream.seek(self._cursor.offset)
            data = stream.read(_MAX_READ_BYTES)
        if not data or b"\n" not in data:
            return []

        complete_end = data.rfind(b"\n") + 1
        complete = data[:complete_end]
        return self._pending_lines(complete, identity, self._cursor.offset)

    def acknowledge(self, pending_line: PendingLine) -> None:
        if self._cursor is None:
            raise RuntimeError("Cannot acknowledge before the tailer is initialized")
        if pending_line.cursor.file_identity != self._cursor.file_identity:
            raise RuntimeError("Cannot acknowledge a line from a different log file")
        if pending_line.cursor.offset < self._cursor.offset:
            return
        self._cursor = pending_line.cursor
        self._save_cursor(self._cursor)

    @staticmethod
    def _pending_lines(data: bytes, identity: str, base_offset: int) -> list[PendingLine]:
        pending: list[PendingLine] = []
        line_start = 0
        for line_end, byte in enumerate(data):
            if byte != ord("\n"):
                continue
            raw_line = data[line_start:line_end].removesuffix(b"\r")
            pending.append(
                PendingLine(
                    raw_line.decode("utf-8"),
                    Cursor(identity, base_offset + line_end + 1),
                )
            )
            line_start = line_end + 1
        return pending

    def _initial_cursor(self, identity: str, size: int) -> Cursor:
        saved = self._load_cursor()
        if saved and saved.file_identity == identity and saved.offset <= size:
            return saved
        if saved is not None:
            return Cursor(identity, 0)
        return Cursor(identity, size)

    def _load_cursor(self) -> Cursor | None:
        try:
            data: Any = json.loads(self._cursor_path.read_text(encoding="utf-8"))
            file_identity = data["file_identity"]
            offset = data["offset"]
            if not isinstance(file_identity, str) or not isinstance(offset, int) or offset < 0:
                return None
            return Cursor(file_identity, offset)
        except FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError:
            return None

    def _save_cursor(self, cursor: Cursor) -> None:
        self._cursor_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._cursor_path.with_suffix(f"{self._cursor_path.suffix}.tmp")
        temporary.write_text(json.dumps(asdict(cursor)), encoding="utf-8")
        os.replace(temporary, self._cursor_path)
