import base64
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mc_bot.quest import (
    SYSTEM_QUEST_OWNER_UUID,
    QuestStore,
    admin_quest_create_command,
    parse_admin_quest_create_result,
    parse_quest_action_result,
    quest_action_command,
    quest_log_nonce,
)
from mc_bot.quest_request import MinecraftQuestStateEvent, parse_quest_state
from mc_bot.quest_ui import quest_guide_embed, quest_listing_embed, quest_log_embed

OWNER_UUID = "22222222-2222-4222-8222-222222222222"
WORKER_UUID = "33333333-3333-4333-8333-333333333333"
EVENT_ID = "11111111-1111-4111-8111-111111111111"
CREATED_ID = "44444444-4444-4444-8444-444444444444"
ACCEPTED_ID = "55555555-5555-4555-8555-555555555555"


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _event(**changes: object) -> MinecraftQuestStateEvent:
    created = datetime(2026, 8, 20, tzinfo=UTC)
    original = MinecraftQuestStateEvent(
        transition_id=CREATED_ID,
        transition_kind="created",
        quest_id=17,
        event_id=EVENT_ID,
        owner_uuid=OWNER_UUID,
        owner_name="Owner",
        worker_uuid=None,
        worker_name=None,
        requested_item_id="minecraft:ancient_debris",
        requested_item_name="古代の残骸",
        requested_count=8,
        reward_item_id="minecraft:diamond",
        reward_item_name="ダイヤモンド",
        reward_count=3,
        fulfillment_hours=24,
        status="open",
        open_expires_at=(created + timedelta(days=7)).isoformat(),
        accepted_deadline=None,
        created_at=created.isoformat(),
        published_at=created.isoformat(),
    )
    return replace(original, **changes)


def test_parses_versioned_quest_state_with_exact_items_and_deadline() -> None:
    created = datetime(2026, 8, 20, tzinfo=UTC)
    created_ms = int(created.timestamp() * 1_000)
    open_expiry = int((created + timedelta(days=7)).timestamp() * 1_000)
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] USAPO_QUEST_STATE|1|"
        f"{CREATED_ID}|created|17|{EVENT_ID}|{OWNER_UUID}|{_encoded('.Owner')}|-|-|"
        f"{_encoded('minecraft:ancient_debris')}|{_encoded('古代の残骸')}|8|"
        f"{_encoded('minecraft:diamond')}|{_encoded('ダイヤモンド')}|3|24|open|"
        f"{open_expiry}|0|{created_ms}|{created_ms}"
    )

    event = parse_quest_state(line)

    assert event is not None
    assert event.quest_id == 17
    assert event.owner_name == ".Owner"
    assert event.requested_count == 8
    assert event.reward_count == 3
    assert event.accepted_deadline is None


def test_rejects_accepted_quest_without_worker() -> None:
    created = datetime(2026, 8, 20, tzinfo=UTC)
    created_ms = int(created.timestamp() * 1_000)
    open_expiry = int((created + timedelta(days=7)).timestamp() * 1_000)
    deadline = int((created + timedelta(days=1)).timestamp() * 1_000)
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] USAPO_QUEST_STATE|1|"
        f"{ACCEPTED_ID}|accepted|17|{EVENT_ID}|{OWNER_UUID}|{_encoded('Owner')}|-|-|"
        f"{_encoded('minecraft:stone')}|{_encoded('石')}|8|"
        f"{_encoded('minecraft:diamond')}|{_encoded('ダイヤモンド')}|3|24|accepted|"
        f"{open_expiry}|{deadline}|{created_ms}|{created_ms}"
    )

    with pytest.raises(ValueError):
        parse_quest_state(line)


def test_parses_invalidated_terminal_state() -> None:
    created = datetime(2026, 8, 20, tzinfo=UTC)
    created_ms = int(created.timestamp() * 1_000)
    open_expiry = int((created + timedelta(days=7)).timestamp() * 1_000)
    line = (
        "[08:24:00] [Server thread/INFO]: [UsapoEventBridge] USAPO_QUEST_STATE|1|"
        f"{ACCEPTED_ID}|invalidated|17|{EVENT_ID}|{OWNER_UUID}|{_encoded('Owner')}|-|-|"
        f"{_encoded('minecraft:stone')}|{_encoded('石')}|8|"
        f"{_encoded('minecraft:diamond')}|{_encoded('ダイヤモンド')}|3|24|cancelled|"
        f"{open_expiry}|0|{created_ms}|{created_ms}"
    )

    event = parse_quest_state(line)

    assert event is not None
    assert event.transition_kind == "invalidated"
    assert event.status == "cancelled"


def test_store_audits_transitions_and_does_not_regress_on_late_state(tmp_path) -> None:
    path = tmp_path / "accounts.db"
    store = QuestStore(path)
    store.initialize()
    created = _event()

    quest, applied = store.apply_state(
        created,
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=None,
        worker_discord_user_id=None,
    )
    duplicate, duplicate_applied = store.apply_state(
        created,
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=None,
        worker_discord_user_id=None,
    )
    accepted = replace(
        created,
        transition_id=ACCEPTED_ID,
        transition_kind="accepted",
        worker_uuid=WORKER_UUID,
        worker_name="Worker",
        status="accepted",
        accepted_deadline="2026-08-21T00:00:00+00:00",
        published_at="2026-08-20T00:01:00+00:00",
    )
    accepted_quest, accepted_applied = store.apply_state(
        accepted,
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=3,
        worker_discord_user_id=2003,
    )
    late = replace(
        created,
        transition_id="66666666-6666-4666-8666-666666666666",
        transition_kind="reopened",
        published_at="2026-08-20T00:00:30+00:00",
    )
    current, late_applied = store.apply_state(
        late,
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=None,
        worker_discord_user_id=None,
    )

    assert applied
    assert quest.status == "open"
    assert duplicate == quest
    assert not duplicate_applied
    assert accepted_applied
    assert accepted_quest.status == "accepted"
    assert not late_applied
    assert current.status == "accepted"
    with sqlite3.connect(path) as connection:
        transitions = connection.execute(
            "SELECT COUNT(*) FROM minecraft_quest_transitions"
        ).fetchone()
    assert transitions == (3,)


def test_snapshot_reuses_current_transition_without_audit_conflict(tmp_path) -> None:
    store = QuestStore(tmp_path / "accounts.db")
    store.initialize()
    event = _event()
    store.apply_state(
        event,
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=None,
        worker_discord_user_id=None,
    )

    refreshed, applied = store.apply_state(
        replace(
            event,
            transition_kind="snapshot",
            owner_name="OwnerNew",
            published_at="2026-08-20T00:02:00+00:00",
        ),
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=None,
        worker_discord_user_id=None,
    )

    assert applied
    assert refreshed.owner_name == "OwnerNew"
    assert refreshed.last_transition_kind == "created"


@pytest.mark.parametrize(
    "changes",
    [
        {"fulfillment_hours": 25},
        {"created_at": "2026-08-19T23:59:59+00:00"},
    ],
)
def test_store_rejects_changes_to_quest_identity_fields(tmp_path, changes) -> None:
    store = QuestStore(tmp_path / "accounts.db")
    store.initialize()
    event = _event()
    store.apply_state(
        event,
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=None,
        worker_discord_user_id=None,
    )

    with pytest.raises(ValueError, match="identity conflict"):
        store.apply_state(
            replace(
                event,
                transition_id="66666666-6666-4666-8666-666666666666",
                transition_kind="snapshot",
                **changes,
            ),
            owner_account_id=2,
            owner_discord_user_id=2002,
            worker_account_id=None,
            worker_discord_user_id=None,
        )


def test_quest_rcon_protocol_requires_name_only_for_accept() -> None:
    command = quest_action_command(
        "accept",
        17,
        WORKER_UUID,
        ACCEPTED_ID,
        player_name=".Worker",
    )
    result = parse_quest_action_result(
        f"Done USAPO_QUEST_ACTION_RESULT|1|{ACCEPTED_ID}|17|completed|accepted|new\n",
        request_id=ACCEPTED_ID,
        quest_id=17,
    )

    assert command == (
        f"usapo-event-bridge quest-accept 17 {WORKER_UUID} {_encoded('.Worker')} {ACCEPTED_ID}"
    )
    assert result.status == "completed"
    assert result.quest_status == "accepted"
    assert not result.duplicate
    with pytest.raises(ValueError):
        quest_action_command("accept", 17, WORKER_UUID, ACCEPTED_ID)

    assert quest_action_command("invalidate", 17, OWNER_UUID, ACCEPTED_ID) == (
        f"usapo-event-bridge quest-invalidate 17 {OWNER_UUID} {ACCEPTED_ID}"
    )
    assert quest_log_nonce(ACCEPTED_ID) == quest_log_nonce(ACCEPTED_ID)
    assert 0 <= quest_log_nonce(ACCEPTED_ID) < 2**64


def test_admin_quest_create_protocol_binds_all_items_counts_and_request_id() -> None:
    command = admin_quest_create_command(
        "minecraft:stone",
        32,
        "minecraft:diamond",
        3,
        24,
        CREATED_ID,
    )
    result = parse_admin_quest_create_result(
        f"Done USAPO_QUEST_CREATE_RESULT|1|{CREATED_ID}|17|completed|new\n",
        request_id=CREATED_ID,
    )

    assert command == (
        "usapo-event-bridge quest-admin-create minecraft:stone 32 "
        f"minecraft:diamond 3 24 {CREATED_ID}"
    )
    assert result.quest_id == 17
    assert result.status == "completed"
    assert result.duplicate is False


def test_system_quest_card_uses_bot_mention_and_has_no_minecraft_owner(tmp_path) -> None:
    event = _event(owner_uuid=SYSTEM_QUEST_OWNER_UUID, owner_name="-")
    store = QuestStore(tmp_path / "quests.db")
    store.initialize()

    quest, _ = store.apply_state(
        event,
        owner_account_id=None,
        owner_discord_user_id=999,
        worker_account_id=None,
        worker_discord_user_id=None,
    )
    card = quest_listing_embed(quest)
    cancelled_log = quest_log_embed(
        replace(quest, status="cancelled", last_transition_kind="cancelled")
    )

    assert quest.is_system_issued
    assert quest.owner_account_id is None
    assert card.fields[0].value == "<@999>"
    assert "理由: 管理者が取消" in (cancelled_log.description or "")
    assert "返却アイテムはありません" in (cancelled_log.footer.text or "")


def test_quest_cards_explain_visibility_claims_and_terminal_result(tmp_path) -> None:
    store = QuestStore(tmp_path / "accounts.db")
    store.initialize()
    quest, _ = store.apply_state(
        _event(),
        owner_account_id=2,
        owner_discord_user_id=2002,
        worker_account_id=None,
        worker_discord_user_id=None,
    )

    card = quest_listing_embed(quest)
    guide = quest_guide_embed()
    completed = replace(
        quest,
        status="completed",
        worker_discord_user_id=2003,
        worker_name="Worker",
    )
    log = quest_log_embed(completed)
    invalidated = quest_log_embed(
        replace(quest, status="cancelled", last_transition_kind="invalidated")
    )

    assert "受注するとカードは掲示板から消えます" in str(card.to_dict())
    assert "Java版・Bedrock版ともMinecraftの `/quest`" in str(guide.to_dict())
    assert "依頼を作る" in str(guide.to_dict())
    assert "自分の依頼・受注" in str(guide.to_dict())
    assert "受取箱を受け取る" in str(guide.to_dict())
    assert "/quest claim" in str(guide.to_dict())
    assert "古代の残骸" in str(log.to_dict())
    assert "ダイヤモンド" in str(log.to_dict())
    assert "Discord連携が確認できず終了" in str(invalidated.to_dict())
