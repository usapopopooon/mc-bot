from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MinecraftAccount:
    id: int
    edition: str
    minecraft_name: str
    server_player_name: str
    player_uuid: str | None
    discord_user_id: int | None
    discord_username: str | None
    managed: bool
    source: str
    status: str
    created_by: int | None
    approval_message_id: int | None


@dataclass(frozen=True, slots=True)
class MinecraftXpOutboxEvent:
    event_id: str
    account_id: int
    discord_user_id: int
    guild_id: int
    minecraft_xp: int
    observed_at: str


class AccountStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS minecraft_accounts (
                    id INTEGER PRIMARY KEY,
                    edition TEXT NOT NULL CHECK (edition IN ('java', 'bedrock')),
                    minecraft_name TEXT NOT NULL,
                    server_player_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    player_uuid TEXT,
                    discord_user_id INTEGER,
                    discord_username TEXT,
                    managed INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL CHECK (source IN ('legacy', 'self', 'admin')),
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'active', 'pending_approval', 'pending_add',
                            'pending_remove', 'rejected', 'missing'
                        )
                    ),
                    created_by INTEGER,
                    approval_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS accounts_discord_user
                    ON minecraft_accounts(discord_user_id);
                CREATE INDEX IF NOT EXISTS accounts_status
                    ON minecraft_accounts(status);
                CREATE TABLE IF NOT EXISTS minecraft_xp_observations (
                    account_id INTEGER PRIMARY KEY
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    current_xp INTEGER NOT NULL CHECK (current_xp >= 0),
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS minecraft_xp_outbox (
                    event_id TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    minecraft_xp INTEGER NOT NULL CHECK (minecraft_xp > 0),
                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS minecraft_xp_outbox_created
                    ON minecraft_xp_outbox(created_at);
                """
            )

    def import_whitelist(self, whitelist_path: Path, bedrock_prefix: str = ".") -> int:
        if not whitelist_path.exists():
            return 0
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("whitelist.json must contain an array")

        imported = 0
        now = _now()
        with self._connect() as connection:
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                player_uuid = entry.get("uuid")
                if not isinstance(name, str) or not name:
                    continue
                is_bedrock = bool(bedrock_prefix and name.startswith(bedrock_prefix)) or (
                    isinstance(player_uuid, str)
                    and player_uuid.lower().startswith("00000000-0000-0000-0009-")
                )
                edition = "bedrock" if is_bedrock else "java"
                minecraft_name = (
                    name.removeprefix(bedrock_prefix) if is_bedrock and bedrock_prefix else name
                )
                cursor = connection.execute(
                    """
                    INSERT INTO minecraft_accounts (
                        edition, minecraft_name, server_player_name, player_uuid,
                        managed, source, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, 'legacy', 'active', ?, ?)
                    ON CONFLICT(server_player_name) DO UPDATE SET
                        player_uuid = COALESCE(excluded.player_uuid, player_uuid),
                        status = CASE
                            WHEN status = 'missing' AND managed = 0 THEN 'active'
                            ELSE status
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (edition, minecraft_name, name, player_uuid, now, now),
                )
                imported += cursor.rowcount
        return imported

    def create_registration(
        self,
        *,
        edition: str,
        minecraft_name: str,
        server_player_name: str,
        discord_user_id: int,
        discord_username: str,
        source: str,
        status: str,
        created_by: int,
    ) -> MinecraftAccount:
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id, status FROM minecraft_accounts
                WHERE server_player_name = ? COLLATE NOCASE
                """,
                (server_player_name,),
            ).fetchone()
            if existing is not None:
                if existing["status"] != "missing":
                    raise ValueError("このMinecraftアカウントはすでに登録されています。")
                connection.execute(
                    """
                    UPDATE minecraft_accounts
                    SET edition = ?, minecraft_name = ?, discord_user_id = ?,
                        discord_username = ?, managed = 1, source = ?, status = ?,
                        created_by = ?, approval_message_id = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        edition,
                        minecraft_name,
                        discord_user_id,
                        discord_username,
                        source,
                        status,
                        created_by,
                        now,
                        existing["id"],
                    ),
                )
                account_id = int(existing["id"])
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO minecraft_accounts (
                        edition, minecraft_name, server_player_name, discord_user_id,
                        discord_username, managed, source, status, created_by,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        edition,
                        minecraft_name,
                        server_player_name,
                        discord_user_id,
                        discord_username,
                        source,
                        status,
                        created_by,
                        now,
                        now,
                    ),
                )
                account_id = int(cursor.lastrowid)
        account = self.get(account_id)
        if account is None:
            raise RuntimeError("Could not load the newly created account")
        return account

    def get(self, account_id: int) -> MinecraftAccount | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM minecraft_accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return _account(row) if row is not None else None

    def find_by_player_name(self, player_name: str) -> MinecraftAccount | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE server_player_name = ? COLLATE NOCASE
                  AND status IN ('active', 'pending_remove')
                """,
                (player_name,),
            ).fetchone()
        return _account(row) if row is not None else None

    def list_for_discord_user(self, discord_user_id: int) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE discord_user_id = ?
                  AND status NOT IN ('rejected', 'missing')
                ORDER BY edition, minecraft_name COLLATE NOCASE
                """,
                (discord_user_id,),
            ).fetchall()
        return [_account(row) for row in rows]

    def list_unlinked(self, limit: int = 25) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE discord_user_id IS NULL AND status = 'active'
                ORDER BY edition, minecraft_name COLLATE NOCASE
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_account(row) for row in rows]

    def list_pending_approvals(self) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE status = 'pending_approval'
                ORDER BY created_at
                """
            ).fetchall()
        return [_account(row) for row in rows]

    def list_pending_actions(self) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE status IN ('pending_add', 'pending_remove')
                ORDER BY updated_at
                """
            ).fetchall()
        return [_account(row) for row in rows]

    def list_whitelist_registrations(self) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE status IN ('active', 'pending_add', 'pending_remove')
                ORDER BY edition, minecraft_name COLLATE NOCASE
                """
            ).fetchall()
        return [_account(row) for row in rows]

    def reconcile_whitelist(self, player_names: list[str]) -> tuple[int, int, int]:
        present = {name.casefold() for name in player_names}
        queued_adds = 0
        completed_adds = 0
        completed_removals = 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, server_player_name, managed, status
                FROM minecraft_accounts
                WHERE status IN ('active', 'pending_add', 'pending_remove')
                """
            ).fetchall()
            now = _now()
            for row in rows:
                is_present = row["server_player_name"].casefold() in present
                new_status: str | None = None
                if row["status"] == "active" and not is_present and row["managed"]:
                    new_status = "pending_add"
                    queued_adds += 1
                elif row["status"] == "pending_add" and is_present:
                    new_status = "active"
                    completed_adds += 1
                elif row["status"] == "pending_remove" and not is_present:
                    new_status = "missing"
                    completed_removals += 1
                if new_status is not None:
                    connection.execute(
                        """
                        UPDATE minecraft_accounts
                        SET status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (new_status, now, row["id"]),
                    )
        return queued_adds, completed_adds, completed_removals

    def list_managed_for_discord_user(self, discord_user_id: int) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE discord_user_id = ? AND managed = 1
                  AND status IN ('active', 'pending_add')
                """,
                (discord_user_id,),
            ).fetchall()
        return [_account(row) for row in rows]

    def list_linked_active(self) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE discord_user_id IS NOT NULL AND status = 'active'
                ORDER BY id
                """
            ).fetchall()
        return [_account(row) for row in rows]

    def observe_minecraft_xp(
        self,
        *,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        current_xp: int,
        observed_at: str,
    ) -> MinecraftXpOutboxEvent | None:
        """観測値更新と正の差分outbox作成を同一transactionで行う。"""
        if current_xp < 0:
            raise ValueError("current_xp must not be negative")
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT current_xp FROM minecraft_xp_observations WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO minecraft_xp_observations (account_id, current_xp, observed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    current_xp = excluded.current_xp,
                    observed_at = excluded.observed_at
                """,
                (account_id, current_xp, observed_at),
            )
            if previous is None or current_xp <= int(previous["current_xp"]):
                return None

            event = MinecraftXpOutboxEvent(
                event_id=str(uuid.uuid4()),
                account_id=account_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                minecraft_xp=current_xp - int(previous["current_xp"]),
                observed_at=observed_at,
            )
            connection.execute(
                """
                INSERT INTO minecraft_xp_outbox (
                    event_id, account_id, discord_user_id, guild_id,
                    minecraft_xp, observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.account_id,
                    event.discord_user_id,
                    event.guild_id,
                    event.minecraft_xp,
                    event.observed_at,
                    _now(),
                ),
            )
        return event

    def list_minecraft_xp_outbox(self, limit: int = 100) -> list[MinecraftXpOutboxEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, account_id, discord_user_id, guild_id,
                       minecraft_xp, observed_at
                FROM minecraft_xp_outbox
                ORDER BY created_at, event_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            MinecraftXpOutboxEvent(
                event_id=row["event_id"],
                account_id=row["account_id"],
                discord_user_id=row["discord_user_id"],
                guild_id=row["guild_id"],
                minecraft_xp=row["minecraft_xp"],
                observed_at=row["observed_at"],
            )
            for row in rows
        ]

    def mark_minecraft_xp_delivered(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM minecraft_xp_outbox WHERE event_id = ?",
                (event_id,),
            )

    def link_existing(
        self,
        account_id: int,
        *,
        discord_user_id: int,
        discord_username: str,
        managed: bool,
        created_by: int,
    ) -> MinecraftAccount:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_accounts
                SET discord_user_id = ?, discord_username = ?, managed = ?,
                    created_by = ?, updated_at = ?
                WHERE id = ? AND discord_user_id IS NULL AND status = 'active'
                """,
                (
                    discord_user_id,
                    discord_username,
                    int(managed),
                    created_by,
                    _now(),
                    account_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("このアカウントはすでに紐付け済みか、利用できません。")
        account = self.get(account_id)
        if account is None:
            raise RuntimeError("Linked account disappeared")
        return account

    def update_status(self, account_id: int, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_accounts
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, _now(), account_id),
            )

    def set_approval_message(self, account_id: int, message_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_accounts
                SET approval_message_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (message_id, _now(), account_id),
            )

    def update_discord_username(self, discord_user_id: int, username: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_accounts
                SET discord_username = ?, updated_at = ?
                WHERE discord_user_id = ? AND discord_username != ?
                """,
                (username, _now(), discord_user_id, username),
            )

    def update_player_uuid(self, account_id: int, player_uuid: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_accounts
                SET player_uuid = ?, updated_at = ?
                WHERE id = ?
                """,
                (player_uuid, _now(), account_id),
            )

    def unlink_protected(self, account_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_accounts
                SET discord_user_id = NULL, discord_username = NULL,
                    created_by = NULL, updated_at = ?
                WHERE id = ? AND managed = 0
                """,
                (_now(), account_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("このアカウントは保護された登録ではありません。")

    def delete_pending(self, account_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM minecraft_accounts
                WHERE id = ? AND status IN ('pending_approval', 'pending_add')
                """,
                (account_id,),
            )

    def count_summary(self) -> tuple[int, int, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('active', 'pending_add', 'pending_remove')
                        THEN 1 ELSE 0 END),
                    SUM(CASE WHEN discord_user_id IS NULL AND status = 'active' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = 'pending_approval' THEN 1 ELSE 0 END)
                FROM minecraft_accounts
                """
            ).fetchone()
        return tuple(int(value or 0) for value in row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection


def _account(row: sqlite3.Row) -> MinecraftAccount:
    return MinecraftAccount(
        id=row["id"],
        edition=row["edition"],
        minecraft_name=row["minecraft_name"],
        server_player_name=row["server_player_name"],
        player_uuid=row["player_uuid"],
        discord_user_id=row["discord_user_id"],
        discord_username=row["discord_username"],
        managed=bool(row["managed"]),
        source=row["source"],
        status=row["status"],
        created_by=row["created_by"],
        approval_message_id=row["approval_message_id"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
