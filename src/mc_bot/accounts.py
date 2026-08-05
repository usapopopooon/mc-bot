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


@dataclass(frozen=True, slots=True)
class MinecraftXpExchangeDelivery:
    exchange_id: str
    level_exchange_id: int
    account_id: int
    discord_user_id: int
    guild_id: int
    player_name: str
    cost_xp: int
    reward_xp: int
    claim_token: str
    reward_applied: bool
    level_completed: bool
    minecraft_notified: bool
    discord_notified: bool


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
                CREATE TABLE IF NOT EXISTS minecraft_advancement_rewards (
                    account_id INTEGER NOT NULL
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    advancement TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    minecraft_xp INTEGER NOT NULL CHECK (minecraft_xp > 0),
                    observed_at TEXT NOT NULL,
                    minecraft_reward_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_reward_delivered IN (0, 1)),
                    PRIMARY KEY (account_id, advancement)
                );
                CREATE TABLE IF NOT EXISTS minecraft_xp_exchange_claims (
                    exchange_id TEXT PRIMARY KEY,
                    claim_token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS minecraft_xp_exchange_deliveries (
                    exchange_id TEXT PRIMARY KEY
                        REFERENCES minecraft_xp_exchange_claims(exchange_id),
                    level_exchange_id INTEGER NOT NULL,
                    account_id INTEGER NOT NULL
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    player_name TEXT NOT NULL,
                    cost_xp INTEGER NOT NULL CHECK (cost_xp > 0),
                    reward_xp INTEGER NOT NULL CHECK (reward_xp > 0),
                    claim_token TEXT NOT NULL,
                    reward_applied INTEGER NOT NULL DEFAULT 0
                        CHECK (reward_applied IN (0, 1)),
                    level_completed INTEGER NOT NULL DEFAULT 0
                        CHECK (level_completed IN (0, 1)),
                    minecraft_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_notified IN (0, 1)),
                    discord_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (discord_notified IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                """
            )
            advancement_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(minecraft_advancement_rewards)")
            }
            if "minecraft_reward_delivered" not in advancement_columns:
                connection.execute(
                    """
                    ALTER TABLE minecraft_advancement_rewards
                    ADD COLUMN minecraft_reward_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_reward_delivered IN (0, 1))
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
        double_in_game_xp: bool = False,
    ) -> MinecraftXpOutboxEvent | None:
        """観測値更新と正の差分outbox作成を同一transactionで行う。

        ``double_in_game_xp`` の場合は、正の差分と同量をRCONで追加する前提で
        追加後の値を観測基準にする。mc-bot自身の追加分を次回また増分として
        扱わないための処理で、outboxにはプレイヤーが得た元の差分だけを入れる。
        """
        if current_xp < 0:
            raise ValueError("current_xp must not be negative")
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT current_xp FROM minecraft_xp_observations WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            gained_xp = (
                current_xp - int(previous["current_xp"])
                if previous is not None and current_xp > int(previous["current_xp"])
                else 0
            )
            expected_xp = current_xp + gained_xp if double_in_game_xp else current_xp
            connection.execute(
                """
                INSERT INTO minecraft_xp_observations (account_id, current_xp, observed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    current_xp = excluded.current_xp,
                    observed_at = excluded.observed_at
                """,
                (account_id, expected_xp, observed_at),
            )
            if gained_xp <= 0:
                return None

            event = MinecraftXpOutboxEvent(
                event_id=str(uuid.uuid4()),
                account_id=account_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                minecraft_xp=gained_xp,
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

    def set_minecraft_xp_observation(
        self, *, account_id: int, current_xp: int, observed_at: str
    ) -> None:
        """XP観測基準だけを更新する。RCONボーナス失敗時の復元にも使う。"""
        if current_xp < 0:
            raise ValueError("current_xp must not be negative")
        with self._connect() as connection:
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

    def reserve_minecraft_xp_exchange_delivery(
        self,
        *,
        exchange_id: str,
        level_exchange_id: int,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        player_name: str,
        cost_xp: int,
        reward_xp: int,
        claim_token: str,
        current_xp: int,
        observed_at: str,
    ) -> bool:
        """交換付与を一度だけ予約し、付与後のXPを観測基準へ先に反映する。"""
        if (
            not exchange_id
            or level_exchange_id <= 0
            or account_id <= 0
            or discord_user_id <= 0
            or guild_id <= 0
            or not player_name
            or cost_xp <= 0
            or reward_xp <= 0
            or not claim_token
            or current_xp < 0
        ):
            raise ValueError("invalid Minecraft XP exchange delivery")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM minecraft_xp_exchange_deliveries WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """
                INSERT INTO minecraft_xp_exchange_deliveries (
                    exchange_id, level_exchange_id, account_id, discord_user_id,
                    guild_id, player_name, cost_xp, reward_xp, claim_token, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange_id,
                    level_exchange_id,
                    account_id,
                    discord_user_id,
                    guild_id,
                    player_name,
                    cost_xp,
                    reward_xp,
                    claim_token,
                    _now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO minecraft_xp_observations (account_id, current_xp, observed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    current_xp = excluded.current_xp,
                    observed_at = excluded.observed_at
                """,
                (account_id, current_xp + reward_xp, observed_at),
            )
        return True

    def get_or_create_minecraft_xp_exchange_claim_token(self, exchange_id: str) -> str:
        if not exchange_id:
            raise ValueError("exchange_id must not be empty")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT claim_token FROM minecraft_xp_exchange_claims WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
            if row is not None:
                return str(row["claim_token"])
            claim_token = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO minecraft_xp_exchange_claims (
                    exchange_id, claim_token, created_at
                ) VALUES (?, ?, ?)
                """,
                (exchange_id, claim_token, _now()),
            )
            return claim_token

    def get_minecraft_xp_exchange_claim_token(self, exchange_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT claim_token FROM minecraft_xp_exchange_claims WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
        return str(row["claim_token"]) if row is not None else None

    def has_minecraft_xp_exchange_delivery(self, exchange_id: str) -> bool:
        with self._connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM minecraft_xp_exchange_deliveries WHERE exchange_id = ?",
                    (exchange_id,),
                ).fetchone()
                is not None
            )

    def mark_minecraft_xp_exchange_reward_applied(self, exchange_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_xp_exchange_deliveries
                SET reward_applied = 1
                WHERE exchange_id = ?
                """,
                (exchange_id,),
            )

    def mark_minecraft_xp_exchange_level_completed(self, exchange_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_xp_exchange_deliveries
                SET level_completed = 1
                WHERE exchange_id = ? AND reward_applied = 1
                """,
                (exchange_id,),
            )

    def mark_minecraft_xp_exchange_notified(self, exchange_id: str, destination: str) -> None:
        columns = {
            "minecraft": "minecraft_notified",
            "discord": "discord_notified",
        }
        column = columns.get(destination)
        if column is None:
            raise ValueError("unknown exchange notification destination")
        with self._connect() as connection:
            connection.execute(
                f"UPDATE minecraft_xp_exchange_deliveries SET {column} = 1 "
                "WHERE exchange_id = ? AND level_completed = 1",
                (exchange_id,),
            )

    def get_minecraft_xp_exchange_delivery(
        self, exchange_id: str
    ) -> MinecraftXpExchangeDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM minecraft_xp_exchange_deliveries WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
        return _minecraft_xp_exchange_delivery(row) if row is not None else None

    def list_pending_minecraft_xp_exchange_deliveries(
        self,
    ) -> list[MinecraftXpExchangeDelivery]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_xp_exchange_deliveries
                WHERE reward_applied = 1
                  AND (
                    level_completed = 0
                    OR minecraft_notified = 0
                    OR discord_notified = 0
                  )
                ORDER BY created_at, exchange_id
                """
            ).fetchall()
        return [_minecraft_xp_exchange_delivery(row) for row in rows]

    def release_minecraft_xp_exchange_delivery(
        self,
        *,
        exchange_id: str,
        account_id: int,
        current_xp: int,
        observed_at: str,
    ) -> None:
        """明示的なRCON失敗時だけ予約と先行観測値を戻す。"""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM minecraft_xp_exchange_deliveries WHERE exchange_id = ?",
                (exchange_id,),
            )
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

    def claim_advancement_reward(
        self,
        *,
        event_id: str,
        account_id: int,
        advancement: str,
        discord_user_id: int,
        guild_id: int,
        minecraft_xp: int,
        observed_at: str,
    ) -> MinecraftXpOutboxEvent | None:
        """進捗報酬を一度だけ記録し、level-bot向けoutboxへ追加する。"""
        if not advancement:
            raise ValueError("advancement must not be empty")
        if minecraft_xp <= 0:
            raise ValueError("minecraft_xp must be positive")
        event = MinecraftXpOutboxEvent(
            event_id=event_id,
            account_id=account_id,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            minecraft_xp=minecraft_xp,
            observed_at=observed_at,
        )
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT event_id FROM minecraft_advancement_rewards
                WHERE account_id = ? AND advancement = ?
                """,
                (account_id, advancement),
            ).fetchone()
            if existing is not None:
                # 同じログ行の再処理なら通知まで再試行する。revoke後などの
                # 新しい達成イベントには報酬を重複付与しない。
                return event if existing["event_id"] == event_id else None
            connection.execute(
                """
                INSERT INTO minecraft_advancement_rewards (
                    account_id, advancement, event_id, discord_user_id,
                    guild_id, minecraft_xp, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    advancement,
                    event_id,
                    discord_user_id,
                    guild_id,
                    minecraft_xp,
                    observed_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO minecraft_xp_outbox (
                    event_id, account_id, discord_user_id, guild_id,
                    minecraft_xp, observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
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

    def is_advancement_minecraft_reward_delivered(self, event_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT minecraft_reward_delivered
                FROM minecraft_advancement_rewards
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            raise ValueError("進捗報酬イベントが見つかりません。")
        return bool(row["minecraft_reward_delivered"])

    def reserve_advancement_minecraft_reward_delivery(
        self,
        *,
        event_id: str,
        account_id: int,
        reward_xp: int,
        observed_at: str,
    ) -> bool:
        """ゲーム内報酬の付与権を原子的に確保し、XP観測基準へ加える。"""
        if reward_xp <= 0:
            raise ValueError("reward_xp must be positive")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_advancement_rewards
                SET minecraft_reward_delivered = 1
                WHERE event_id = ? AND account_id = ?
                  AND minecraft_reward_delivered = 0
                """,
                (event_id, account_id),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    """
                    SELECT minecraft_reward_delivered
                    FROM minecraft_advancement_rewards
                    WHERE event_id = ? AND account_id = ?
                    """,
                    (event_id, account_id),
                ).fetchone()
                if row is not None and row["minecraft_reward_delivered"]:
                    return False
                raise ValueError("進捗報酬イベントが見つかりません。")
            connection.execute(
                """
                UPDATE minecraft_xp_observations
                SET current_xp = current_xp + ?, observed_at = ?
                WHERE account_id = ?
                """,
                (reward_xp, observed_at, account_id),
            )
        return True

    def release_advancement_minecraft_reward_delivery(
        self,
        *,
        event_id: str,
        account_id: int,
        reward_xp: int,
    ) -> None:
        """コマンドが明確に失敗した場合だけ付与権とXP観測基準を戻す。"""
        if reward_xp <= 0:
            raise ValueError("reward_xp must be positive")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_advancement_rewards
                SET minecraft_reward_delivered = 0
                WHERE event_id = ? AND account_id = ?
                  AND minecraft_reward_delivered = 1
                """,
                (event_id, account_id),
            )
            if cursor.rowcount != 1:
                return
            connection.execute(
                """
                UPDATE minecraft_xp_observations
                SET current_xp = MAX(current_xp - ?, 0)
                WHERE account_id = ?
                """,
                (reward_xp, account_id),
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


def _minecraft_xp_exchange_delivery(row: sqlite3.Row) -> MinecraftXpExchangeDelivery:
    return MinecraftXpExchangeDelivery(
        exchange_id=str(row["exchange_id"]),
        level_exchange_id=int(row["level_exchange_id"]),
        account_id=int(row["account_id"]),
        discord_user_id=int(row["discord_user_id"]),
        guild_id=int(row["guild_id"]),
        player_name=str(row["player_name"]),
        cost_xp=int(row["cost_xp"]),
        reward_xp=int(row["reward_xp"]),
        claim_token=str(row["claim_token"]),
        reward_applied=bool(row["reward_applied"]),
        level_completed=bool(row["level_completed"]),
        minecraft_notified=bool(row["minecraft_notified"]),
        discord_notified=bool(row["discord_notified"]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
