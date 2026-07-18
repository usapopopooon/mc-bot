from mc_bot.events import EventType, LogEvent, parse_log_line


def test_parses_java_and_bedrock_chat() -> None:
    assert parse_log_line(
        "[12:34:56] [Server thread/INFO]: <.BedrockPlayer> こんにちは"
    ) == LogEvent(EventType.CHAT, ".BedrockPlayer", "こんにちは")
    assert parse_log_line(
        "[12:34:56] [Server thread/INFO]: [Not Secure] <Steve> hello"
    ) == LogEvent(EventType.CHAT, "Steve", "hello")


def test_parses_join_and_leave() -> None:
    assert parse_log_line("[12:34:56] [Server thread/INFO]: Steve joined the game") == LogEvent(
        EventType.JOIN, "Steve"
    )
    assert parse_log_line("[12:35:56] [Server thread/INFO]: Steve left the game") == LogEvent(
        EventType.LEAVE, "Steve"
    )


def test_parses_all_advancement_wording() -> None:
    for wording in (
        "has made the advancement",
        "has completed the challenge",
        "has reached the goal",
    ):
        assert parse_log_line(
            f"[12:34:56] [Server thread/INFO]: Steve {wording} [Stone Age]"
        ) == LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age")


def test_ignores_unrelated_log_line() -> None:
    assert parse_log_line("[12:34:56] [Server thread/INFO]: Saving the game") is None
