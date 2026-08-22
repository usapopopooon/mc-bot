from __future__ import annotations

import base64
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from mc_bot.quest_request import MinecraftQuestStateEvent
from mc_bot.translations import MinecraftItemTranslator

_ITEM_TRANSLATOR = MinecraftItemTranslator.load()
_QUEST_LOG_NONCE_NAMESPACE = uuid.UUID("1e5b612f-49b1-41af-b8df-3ada33187c48")
SYSTEM_QUEST_OWNER_UUID = "00000000-0000-0000-0000-000000000000"

type QuestActionStatus = Literal[
    "completed",
    "unknown",
    "unavailable",
    "own_quest",
    "not_assignee",
    "not_cancellable",
    "expired",
    "item_mismatch",
    "player_offline",
    "pending_recovered",
    "storage_error",
    "invalid_request",
]
type AdminQuestCreateStatus = Literal[
    "completed",
    "invalid_requested_item",
    "invalid_requested_count",
    "invalid_reward_item",
    "invalid_reward_count",
    "invalid_hours",
    "storage_error",
    "invalid_request",
]


@dataclass(frozen=True, slots=True)
class Quest:
    quest_id: int
    event_id: str
    last_transition_id: str
    last_transition_kind: str
    owner_account_id: int | None
    owner_discord_user_id: int | None
    owner_uuid: str
    owner_name: str
    worker_account_id: int | None
    worker_discord_user_id: int | None
    worker_uuid: str | None
    worker_name: str | None
    requested_item_id: str
    requested_item_name: str
    requested_count: int
    reward_item_id: str
    reward_item_name: str
    reward_count: int
    fulfillment_hours: int
    status: str
    open_expires_at: str
    accepted_deadline: str | None
    discord_message_id: int | None
    discord_log_delivery_attempted: bool
    discord_log_notified: bool
    created_at: str
    published_at: str
    updated_at: str

    @property
    def display_requested_item_name(self) -> str:
        return _ITEM_TRANSLATOR.translate(self.requested_item_id, self.requested_item_name)

    @property
    def display_reward_item_name(self) -> str:
        return _ITEM_TRANSLATOR.translate(self.reward_item_id, self.reward_item_name)

    @property
    def is_system_issued(self) -> bool:
        return self.owner_uuid == SYSTEM_QUEST_OWNER_UUID


@dataclass(frozen=True, slots=True)
class QuestActionResult:
    status: QuestActionStatus
    quest_status: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class AdminQuestCreateResult:
    request_id: str
    quest_id: int
    status: AdminQuestCreateStatus
    duplicate: bool


class QuestStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS minecraft_quests (
                    quest_id INTEGER PRIMARY KEY CHECK (quest_id > 0),
                    event_id TEXT NOT NULL UNIQUE,
                    last_transition_id TEXT NOT NULL,
                    last_transition_kind TEXT NOT NULL,
                    owner_account_id INTEGER,
                    owner_discord_user_id INTEGER,
                    owner_uuid TEXT NOT NULL,
                    owner_name TEXT NOT NULL,
                    worker_account_id INTEGER,
                    worker_discord_user_id INTEGER,
                    worker_uuid TEXT,
                    worker_name TEXT,
                    requested_item_id TEXT NOT NULL,
                    requested_item_name TEXT NOT NULL,
                    requested_count INTEGER NOT NULL CHECK (requested_count > 0),
                    reward_item_id TEXT NOT NULL,
                    reward_item_name TEXT NOT NULL,
                    reward_count INTEGER NOT NULL CHECK (reward_count > 0),
                    fulfillment_hours INTEGER NOT NULL CHECK (
                        fulfillment_hours BETWEEN 1 AND 72
                    ),
                    status TEXT NOT NULL CHECK (
                        status IN ('open', 'accepted', 'completed', 'cancelled')
                    ),
                    open_expires_at TEXT NOT NULL,
                    accepted_deadline TEXT,
                    discord_message_id INTEGER,
                    discord_log_delivery_attempted INTEGER NOT NULL DEFAULT 0 CHECK (
                        discord_log_delivery_attempted IN (0, 1)
                    ),
                    discord_log_notified INTEGER NOT NULL DEFAULT 0 CHECK (
                        discord_log_notified IN (0, 1)
                    ),
                    created_at TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS minecraft_quest_status
                    ON minecraft_quests(status, quest_id);
                CREATE INDEX IF NOT EXISTS minecraft_quest_owner
                    ON minecraft_quests(owner_discord_user_id, status);
                CREATE INDEX IF NOT EXISTS minecraft_quest_worker
                    ON minecraft_quests(worker_discord_user_id, status);
                CREATE TABLE IF NOT EXISTS minecraft_quest_transitions (
                    transition_id TEXT PRIMARY KEY,
                    quest_id INTEGER NOT NULL,
                    transition_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    FOREIGN KEY (quest_id) REFERENCES minecraft_quests(quest_id)
                );
                CREATE INDEX IF NOT EXISTS minecraft_quest_transition_quest
                    ON minecraft_quest_transitions(quest_id, published_at);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(minecraft_quests)")
            }
            if "discord_log_delivery_attempted" not in columns:
                connection.execute(
                    """
                    ALTER TABLE minecraft_quests
                    ADD COLUMN discord_log_delivery_attempted INTEGER NOT NULL DEFAULT 0
                    CHECK (discord_log_delivery_attempted IN (0, 1))
                    """
                )

    def apply_state(
        self,
        event: MinecraftQuestStateEvent,
        *,
        owner_account_id: int | None,
        owner_discord_user_id: int | None,
        worker_account_id: int | None,
        worker_discord_user_id: int | None,
    ) -> tuple[Quest, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_transition = connection.execute(
                "SELECT * FROM minecraft_quest_transitions WHERE transition_id = ?",
                (event.transition_id,),
            ).fetchone()
            existing = connection.execute(
                "SELECT * FROM minecraft_quests WHERE quest_id = ? OR event_id = ?",
                (event.quest_id, event.event_id),
            ).fetchone()
            if existing is not None and (
                int(existing["quest_id"]) != event.quest_id
                or str(existing["event_id"]) != event.event_id
                or str(existing["owner_uuid"]) != event.owner_uuid
                or str(existing["requested_item_id"]) != event.requested_item_id
                or int(existing["requested_count"]) != event.requested_count
                or str(existing["reward_item_id"]) != event.reward_item_id
                or int(existing["reward_count"]) != event.reward_count
                or int(existing["fulfillment_hours"]) != event.fulfillment_hours
                or str(existing["created_at"]) != event.created_at
            ):
                raise ValueError("quest state identity conflict")
            if existing_transition is not None:
                if (
                    int(existing_transition["quest_id"]) != event.quest_id
                    or str(existing_transition["status"]) != event.status
                ):
                    raise ValueError("quest transition idempotency conflict")
                if existing is None:
                    raise ValueError("quest transition has no quest")
                if event.transition_kind == "snapshot":
                    connection.execute(
                        """
                        UPDATE minecraft_quests SET
                            owner_account_id = ?, owner_discord_user_id = ?, owner_name = ?,
                            worker_account_id = ?, worker_discord_user_id = ?,
                            worker_uuid = ?, worker_name = ?, requested_item_name = ?,
                            reward_item_name = ?, open_expires_at = ?, accepted_deadline = ?,
                            published_at = ?, updated_at = ?
                        WHERE quest_id = ? AND published_at <= ?
                        """,
                        (
                            owner_account_id,
                            owner_discord_user_id,
                            event.owner_name,
                            worker_account_id,
                            worker_discord_user_id,
                            event.worker_uuid,
                            event.worker_name,
                            event.requested_item_name,
                            event.reward_item_name,
                            event.open_expires_at,
                            event.accepted_deadline,
                            event.published_at,
                            _now(),
                            event.quest_id,
                            event.published_at,
                        ),
                    )
                    refreshed = connection.execute(
                        "SELECT * FROM minecraft_quests WHERE quest_id = ?",
                        (event.quest_id,),
                    ).fetchone()
                    assert refreshed is not None
                    return _quest(refreshed), True
                if str(existing_transition["transition_kind"]) != event.transition_kind:
                    raise ValueError("quest transition idempotency conflict")
                return _quest(existing), False
            now = _now()
            should_apply = existing is None or event.published_at >= str(existing["published_at"])
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO minecraft_quests (
                        quest_id, event_id, last_transition_id, last_transition_kind,
                        owner_account_id, owner_discord_user_id, owner_uuid, owner_name,
                        worker_account_id, worker_discord_user_id, worker_uuid, worker_name,
                        requested_item_id, requested_item_name, requested_count,
                        reward_item_id, reward_item_name, reward_count, fulfillment_hours,
                        status, open_expires_at, accepted_deadline, created_at,
                        published_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?)
                    """,
                    _state_values(
                        event,
                        owner_account_id,
                        owner_discord_user_id,
                        worker_account_id,
                        worker_discord_user_id,
                        now,
                    ),
                )
            elif should_apply:
                connection.execute(
                    """
                    UPDATE minecraft_quests SET
                        last_transition_id = ?, last_transition_kind = ?,
                        owner_account_id = ?, owner_discord_user_id = ?, owner_name = ?,
                        worker_account_id = ?, worker_discord_user_id = ?,
                        worker_uuid = ?, worker_name = ?,
                        requested_item_name = ?, reward_item_name = ?, fulfillment_hours = ?,
                        status = ?, open_expires_at = ?, accepted_deadline = ?,
                        published_at = ?, updated_at = ?
                    WHERE quest_id = ?
                    """,
                    (
                        event.transition_id,
                        event.transition_kind,
                        owner_account_id,
                        owner_discord_user_id,
                        event.owner_name,
                        worker_account_id,
                        worker_discord_user_id,
                        event.worker_uuid,
                        event.worker_name,
                        event.requested_item_name,
                        event.reward_item_name,
                        event.fulfillment_hours,
                        event.status,
                        event.open_expires_at,
                        event.accepted_deadline,
                        event.published_at,
                        now,
                        event.quest_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO minecraft_quest_transitions (
                    transition_id, quest_id, transition_kind, status, published_at, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.transition_id,
                    event.quest_id,
                    event.transition_kind,
                    event.status,
                    event.published_at,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM minecraft_quests WHERE quest_id = ?", (event.quest_id,)
            ).fetchone()
            assert row is not None
            return _quest(row), should_apply

    def get(self, quest_id: int) -> Quest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM minecraft_quests WHERE quest_id = ?", (quest_id,)
            ).fetchone()
        return _quest(row) if row is not None else None

    def list_open(self) -> list[Quest]:
        return self._list("status = 'open'", ())

    def list_nonopen_with_discord_message(self) -> list[Quest]:
        return self._list("status != 'open' AND discord_message_id IS NOT NULL", ())

    def list_active_for_discord_user(self, user_id: int) -> list[Quest]:
        return self._list(
            "status IN ('open', 'accepted') AND "
            "(owner_discord_user_id = ? OR worker_discord_user_id = ?)",
            (user_id, user_id),
        )

    def list_active_for_account(self, account_id: int) -> list[Quest]:
        return self._list(
            "status IN ('open', 'accepted') AND (owner_account_id = ? OR worker_account_id = ?)",
            (account_id, account_id),
        )

    def list_terminal_unnotified(self) -> list[Quest]:
        return self._list("status IN ('completed', 'cancelled') AND discord_log_notified = 0", ())

    def set_discord_message(self, quest_id: int, message_id: int | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE minecraft_quests SET discord_message_id = ?, updated_at = ? "
                "WHERE quest_id = ?",
                (message_id, _now(), quest_id),
            )

    def mark_discord_log_notified(self, quest_id: int, transition_id: str) -> None:
        normalized = str(uuid.UUID(transition_id))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_quests SET discord_log_notified = 1, updated_at = ?
                WHERE quest_id = ? AND last_transition_id = ?
                  AND status IN ('completed', 'cancelled')
                """,
                (_now(), quest_id, normalized),
            )

    def mark_discord_log_delivery_attempted(self, quest_id: int, transition_id: str) -> None:
        normalized = str(uuid.UUID(transition_id))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_quests
                SET discord_log_delivery_attempted = 1, updated_at = ?
                WHERE quest_id = ? AND last_transition_id = ?
                  AND status IN ('completed', 'cancelled')
                """,
                (_now(), quest_id, normalized),
            )

    def _list(self, where: str, parameters: tuple[object, ...]) -> list[Quest]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM minecraft_quests WHERE {where} ORDER BY quest_id DESC",
                parameters,
            ).fetchall()
        return [_quest(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection


def quest_action_command(
    action: str,
    quest_id: int,
    player_uuid: str,
    request_id: str,
    *,
    player_name: str | None = None,
) -> str:
    if action not in {"accept", "submit", "abandon", "cancel", "invalidate"} or quest_id <= 0:
        raise ValueError("invalid quest action")
    player = str(uuid.UUID(player_uuid))
    request = str(uuid.UUID(request_id))
    parts = ["usapo-event-bridge", f"quest-{action}", str(quest_id), player]
    if action == "accept":
        if player_name is None or not player_name.strip():
            raise ValueError("quest accept requires a player name")
        encoded = base64.urlsafe_b64encode(player_name.encode()).decode().rstrip("=")
        parts.append(encoded)
    elif player_name is not None:
        raise ValueError("unexpected quest player name")
    parts.append(request)
    return " ".join(parts)


def admin_quest_create_command(
    requested_item_id: str,
    requested_count: int,
    reward_item_id: str,
    reward_count: int,
    fulfillment_hours: int,
    request_id: str,
) -> str:
    item_pattern = re.compile(r"minecraft:[a-z0-9_]+")
    if (
        item_pattern.fullmatch(requested_item_id) is None
        or item_pattern.fullmatch(reward_item_id) is None
        or requested_count <= 0
        or reward_count <= 0
        or not 1 <= fulfillment_hours <= 72
    ):
        raise ValueError("invalid admin quest")
    request = str(uuid.UUID(request_id))
    return " ".join(
        (
            "usapo-event-bridge",
            "quest-admin-create",
            requested_item_id,
            str(requested_count),
            reward_item_id,
            str(reward_count),
            str(fulfillment_hours),
            request,
        )
    )


def quest_log_nonce(transition_id: str) -> int:
    normalized = str(uuid.UUID(transition_id))
    return uuid.uuid5(_QUEST_LOG_NONCE_NAMESPACE, normalized).int & ((1 << 64) - 1)


def parse_quest_action_result(
    response: str, *, request_id: str, quest_id: int
) -> QuestActionResult:
    prefix = "USAPO_QUEST_ACTION_RESULT|1|"
    marker = response.strip().find(prefix)
    if marker < 0:
        raise ValueError("quest action result is missing")
    fields = response.strip()[marker + len(prefix) :].split("|")
    if len(fields) != 5:
        raise ValueError("quest action result is malformed")
    if str(uuid.UUID(fields[0])) != str(uuid.UUID(request_id)) or int(fields[1]) != quest_id:
        raise ValueError("quest action result does not match request")
    statuses = {
        "completed",
        "unknown",
        "unavailable",
        "own_quest",
        "not_assignee",
        "not_cancellable",
        "expired",
        "item_mismatch",
        "player_offline",
        "pending_recovered",
        "storage_error",
        "invalid_request",
    }
    quest_statuses = {"open", "accepted", "completed", "cancelled", "unknown"}
    duplicates = {"new": False, "duplicate": True}
    if fields[2] not in statuses or fields[3] not in quest_statuses or fields[4] not in duplicates:
        raise ValueError("quest action result contains an unknown state")
    return QuestActionResult(
        status=cast("QuestActionStatus", fields[2]),
        quest_status=fields[3],
        duplicate=duplicates[fields[4]],
    )


def parse_admin_quest_create_result(response: str, *, request_id: str) -> AdminQuestCreateResult:
    prefix = "USAPO_QUEST_CREATE_RESULT|1|"
    marker = response.strip().find(prefix)
    if marker < 0:
        raise ValueError("admin quest create result is missing")
    fields = response.strip()[marker + len(prefix) :].split("|")
    if len(fields) != 4:
        raise ValueError("admin quest create result is malformed")
    normalized_request_id = str(uuid.UUID(request_id))
    if str(uuid.UUID(fields[0])) != normalized_request_id:
        raise ValueError("admin quest create result does not match request")
    quest_id = int(fields[1])
    statuses = {
        "completed",
        "invalid_requested_item",
        "invalid_requested_count",
        "invalid_reward_item",
        "invalid_reward_count",
        "invalid_hours",
        "storage_error",
        "invalid_request",
    }
    duplicates = {"new": False, "duplicate": True}
    if fields[2] not in statuses or fields[3] not in duplicates:
        raise ValueError("admin quest create result contains an unknown state")
    if (fields[2] == "completed") != (quest_id > 0):
        raise ValueError("admin quest create result contains an invalid quest id")
    return AdminQuestCreateResult(
        request_id=normalized_request_id,
        quest_id=quest_id,
        status=cast("AdminQuestCreateStatus", fields[2]),
        duplicate=duplicates[fields[3]],
    )


def _state_values(
    event: MinecraftQuestStateEvent,
    owner_account_id: int | None,
    owner_discord_user_id: int | None,
    worker_account_id: int | None,
    worker_discord_user_id: int | None,
    now: str,
) -> tuple[object, ...]:
    return (
        event.quest_id,
        event.event_id,
        event.transition_id,
        event.transition_kind,
        owner_account_id,
        owner_discord_user_id,
        event.owner_uuid,
        event.owner_name,
        worker_account_id,
        worker_discord_user_id,
        event.worker_uuid,
        event.worker_name,
        event.requested_item_id,
        event.requested_item_name,
        event.requested_count,
        event.reward_item_id,
        event.reward_item_name,
        event.reward_count,
        event.fulfillment_hours,
        event.status,
        event.open_expires_at,
        event.accepted_deadline,
        event.created_at,
        event.published_at,
        now,
    )


def _quest(row: sqlite3.Row) -> Quest:
    return Quest(
        quest_id=int(row["quest_id"]),
        event_id=str(row["event_id"]),
        last_transition_id=str(row["last_transition_id"]),
        last_transition_kind=str(row["last_transition_kind"]),
        owner_account_id=row["owner_account_id"],
        owner_discord_user_id=row["owner_discord_user_id"],
        owner_uuid=str(row["owner_uuid"]),
        owner_name=str(row["owner_name"]),
        worker_account_id=row["worker_account_id"],
        worker_discord_user_id=row["worker_discord_user_id"],
        worker_uuid=row["worker_uuid"],
        worker_name=row["worker_name"],
        requested_item_id=str(row["requested_item_id"]),
        requested_item_name=str(row["requested_item_name"]),
        requested_count=int(row["requested_count"]),
        reward_item_id=str(row["reward_item_id"]),
        reward_item_name=str(row["reward_item_name"]),
        reward_count=int(row["reward_count"]),
        fulfillment_hours=int(row["fulfillment_hours"]),
        status=str(row["status"]),
        open_expires_at=str(row["open_expires_at"]),
        accepted_deadline=row["accepted_deadline"],
        discord_message_id=row["discord_message_id"],
        discord_log_delivery_attempted=bool(row["discord_log_delivery_attempted"]),
        discord_log_notified=bool(row["discord_log_notified"]),
        created_at=str(row["created_at"]),
        published_at=str(row["published_at"]),
        updated_at=str(row["updated_at"]),
    )


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


__all__ = [
    "SYSTEM_QUEST_OWNER_UUID",
    "AdminQuestCreateResult",
    "Quest",
    "QuestActionResult",
    "QuestStore",
    "admin_quest_create_command",
    "parse_admin_quest_create_result",
    "parse_quest_action_result",
    "quest_action_command",
    "quest_log_nonce",
]
