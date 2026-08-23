from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

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
        self._stream: BinaryIO | None = None
        self._stream_identity: str | None = None

    async def lines(self) -> AsyncIterator[PendingLine]:
        while True:
            for line in await asyncio.to_thread(self.poll):
                yield line
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def validate(self) -> None:
        try:
            log_stat = self._log_path.stat()
        except FileNotFoundError:
            raise FileNotFoundError(f"Minecraft log does not exist: {self._log_path}") from None
        except PermissionError as error:
            raise PermissionError(
                f"Minecraft log is not readable by the bot user: {self._log_path}"
            ) from error
        if not stat.S_ISREG(log_stat.st_mode):
            raise FileNotFoundError(f"Minecraft log is not a regular file: {self._log_path}")
        with self._log_path.open("rb"):
            pass
        self._cursor_path.parent.mkdir(parents=True, exist_ok=True)

    def poll(self) -> list[PendingLine]:
        try:
            log_stat = self._log_path.stat()
        except FileNotFoundError:
            return []
        if not stat.S_ISREG(log_stat.st_mode):
            return []

        identity = f"{log_stat.st_dev}:{log_stat.st_ino}"
        if self._cursor is None:
            self._cursor = self._initial_cursor(identity, log_stat.st_size)
            self._save_cursor(self._cursor)

        if self._stream is not None and self._stream_identity != self._cursor.file_identity:
            self._close_stream()

        if self._stream is not None and self._cursor.file_identity != identity:
            pending = self._read_pending(self._stream, self._cursor)
            if pending:
                return pending
            self._close_stream()
            self._cursor = Cursor(identity, 0)
            self._save_cursor(self._cursor)

        if self._cursor.file_identity != identity or log_stat.st_size < self._cursor.offset:
            self._close_stream()
            self._cursor = Cursor(identity, 0)
            self._save_cursor(self._cursor)

        if self._stream is None and not self._open_current_stream(identity):
            return []
        if self._stream is None:
            return []
        return self._read_pending(self._stream, self._cursor)

    def close(self) -> None:
        self._close_stream()

    def _open_current_stream(self, expected_identity: str) -> bool:
        stream = self._log_path.open("rb")
        stream_stat = os.fstat(stream.fileno())
        identity = f"{stream_stat.st_dev}:{stream_stat.st_ino}"
        if identity != expected_identity:
            stream.close()
            return False
        self._stream = stream
        self._stream_identity = identity
        return True

    def _close_stream(self) -> None:
        if self._stream is not None:
            self._stream.close()
        self._stream = None
        self._stream_identity = None

    @staticmethod
    def _read_pending(stream: BinaryIO, cursor: Cursor) -> list[PendingLine]:
        stream.seek(cursor.offset)
        data = stream.read(_MAX_READ_BYTES)
        if not data or b"\n" not in data:
            return []

        complete_end = data.rfind(b"\n") + 1
        complete = data[:complete_end]
        return LogTailer._pending_lines(complete, cursor.file_identity, cursor.offset)

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
