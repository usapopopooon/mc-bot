from pathlib import Path

from mc_bot.tailer import LogTailer


def test_starts_at_end_then_reads_appended_lines(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    cursor = tmp_path / "cursor.json"
    log.write_text("old line\n", encoding="utf-8")
    tailer = LogTailer(log, cursor)

    assert tailer.poll() == []
    with log.open("a", encoding="utf-8") as stream:
        stream.write("new line\n")
    pending = tailer.poll()
    assert [line.text for line in pending] == ["new line"]
    tailer.acknowledge(pending[0])


def test_resumes_from_saved_cursor(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    cursor = tmp_path / "cursor.json"
    log.write_text("old line\n", encoding="utf-8")
    first = LogTailer(log, cursor)
    assert first.poll() == []

    with log.open("a", encoding="utf-8") as stream:
        stream.write("after restart\n")
    second = LogTailer(log, cursor)
    assert [line.text for line in second.poll()] == ["after restart"]


def test_waits_for_complete_utf8_line(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    cursor = tmp_path / "cursor.json"
    log.write_bytes(b"")
    tailer = LogTailer(log, cursor)
    assert tailer.poll() == []

    with log.open("ab") as stream:
        stream.write("こんにちは".encode())
    assert tailer.poll() == []
    with log.open("ab") as stream:
        stream.write(b"\n")
    assert [line.text for line in tailer.poll()] == ["こんにちは"]


def test_unacknowledged_line_is_replayed_after_restart(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    cursor = tmp_path / "cursor.json"
    log.write_text("old line\n", encoding="utf-8")
    first = LogTailer(log, cursor)
    assert first.poll() == []
    with log.open("a", encoding="utf-8") as stream:
        stream.write("must retry\n")
    assert [line.text for line in first.poll()] == ["must retry"]

    restarted = LogTailer(log, cursor)
    assert [line.text for line in restarted.poll()] == ["must retry"]


def test_reads_new_log_from_start_when_rotation_happened_while_stopped(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    rotated = tmp_path / "previous.log"
    cursor = tmp_path / "cursor.json"
    log.write_text("old line\n", encoding="utf-8")
    first = LogTailer(log, cursor)
    assert first.poll() == []

    log.rename(rotated)
    log.write_text("during downtime\n", encoding="utf-8")
    restarted = LogTailer(log, cursor)
    assert [line.text for line in restarted.poll()] == ["during downtime"]
