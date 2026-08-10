import pytest

from mc_bot.activity import ActivityKind, MinecraftActivityEvent, parse_activity_event


def test_parses_uuid_based_plugin_activity_event() -> None:
    line = (
        "[00:00:01] [Server thread/INFO]: [UsapoEventBridge] USAPO_ACTIVITY|1|"
        "11111111-1111-1111-1111-111111111111|fishing|"
        "22222222-2222-2222-2222-222222222222|Lll1a2kxOTkx|1|1786406400000"
    )

    assert parse_activity_event(line) == MinecraftActivityEvent(
        event_id="11111111-1111-1111-1111-111111111111",
        kind=ActivityKind.FISHING,
        player_uuid="22222222-2222-2222-2222-222222222222",
        player_name=".Yuki1991",
        amount=1,
        occurred_at="2026-08-11T00:00:00+00:00",
    )


def test_ignores_unrelated_server_log() -> None:
    assert parse_activity_event("[00:00:01] [Server thread/INFO]: Steve joined the game") is None


def test_parses_batched_natural_experience_amount() -> None:
    line = (
        "[00:00:01] [Server thread/INFO]: [UsapoEventBridge] USAPO_ACTIVITY|1|"
        "11111111-1111-1111-1111-111111111111|experience|"
        "22222222-2222-2222-2222-222222222222|U3RldmU|37|1786406400000"
    )

    event = parse_activity_event(line)

    assert event is not None
    assert event.kind is ActivityKind.EXPERIENCE
    assert event.amount == 37


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-uuid|fishing|22222222-2222-2222-2222-222222222222|U3RldmU|1|1",
        "11111111-1111-1111-1111-111111111111|mining|"
        "22222222-2222-2222-2222-222222222222|U3RldmU|1|1",
        "11111111-1111-1111-1111-111111111111|fishing|not-a-uuid|U3RldmU|1|1",
        "11111111-1111-1111-1111-111111111111|fishing|22222222-2222-2222-2222-222222222222|***|1|1",
        "11111111-1111-1111-1111-111111111111|experience|"
        "22222222-2222-2222-2222-222222222222|U3RldmU|0|1",
    ],
)
def test_rejects_malformed_plugin_activity_event(payload: str) -> None:
    line = f"[00:00:01] [Server thread/INFO]: [UsapoEventBridge] USAPO_ACTIVITY|1|{payload}"

    with pytest.raises(ValueError, match="Minecraft activity event"):
        parse_activity_event(line)
