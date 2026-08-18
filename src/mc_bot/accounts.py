from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

WHITELIST_RETRY_LIMIT = 5
ITEM_GACHA_NOTIFICATION_RETRY_LIMIT = 5


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
    whitelist_retry_count: int = 0
    whitelist_last_error: str | None = None


@dataclass(frozen=True, slots=True)
class MinecraftXpOutboxEvent:
    event_id: str
    account_id: int
    discord_user_id: int
    guild_id: int
    minecraft_xp: int
    observed_at: str


@dataclass(frozen=True, slots=True)
class FishingComboRewardEvent:
    event_id: str
    account_id: int
    discord_user_id: int
    guild_id: int
    catch_count: int
    combo_count: int
    reward_xp: int
    observed_at: str
    reward_delivered: bool
    audit_delivered: bool
    minecraft_public_delivered: bool = False
    discord_public_delivered: bool = False


@dataclass(frozen=True, slots=True)
class WoodcuttingComboRewardEvent:
    event_id: str
    account_id: int
    discord_user_id: int
    guild_id: int
    log_count: int
    combo_count: int
    reward_xp: int
    observed_at: str
    reward_delivered: bool
    audit_delivered: bool
    minecraft_public_delivered: bool = False
    discord_public_delivered: bool = False


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


@dataclass(frozen=True, slots=True)
class MinecraftResourceExchangeDelivery:
    exchange_id: str
    level_exchange_id: int
    account_id: int
    discord_user_id: int
    guild_id: int
    player_name: str
    item_id: str
    item_name: str
    item_count: int
    cost_xp: int
    claim_token: str
    reward_applied: bool
    level_completed: bool
    minecraft_notified: bool
    minecraft_public_notified: bool
    discord_notified: bool


@dataclass(frozen=True, slots=True)
class MinecraftItemGachaDraw:
    draw_id: str
    guild_id: int
    discord_user_id: int
    account_id: int
    player_name: str
    draw_day: str
    draw_number: int
    draw_kind: str
    draw_category: str
    cost_xp: int
    tier: str
    reward_key: str
    item_spec: str
    item_name: str
    item_count: int
    status: str
    minecraft_notified: bool
    discord_notified: bool
    minecraft_notification_attempts: int
    discord_notification_attempts: int
    created_at: str
    updated_at: str


class MinecraftItemGachaDailyLimitReached(RuntimeError):
    """Raised when all item-gacha slots for a user and JST day are used."""


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
                    whitelist_retry_count INTEGER NOT NULL DEFAULT 0
                        CHECK (whitelist_retry_count >= 0),
                    whitelist_last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS accounts_discord_user
                    ON minecraft_accounts(discord_user_id);
                CREATE INDEX IF NOT EXISTS accounts_status
                    ON minecraft_accounts(status);
                CREATE INDEX IF NOT EXISTS accounts_player_uuid
                    ON minecraft_accounts(player_uuid COLLATE NOCASE);
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
                CREATE TABLE IF NOT EXISTS minecraft_resource_exchange_claims (
                    exchange_id TEXT PRIMARY KEY,
                    claim_token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS minecraft_resource_exchange_deliveries (
                    exchange_id TEXT PRIMARY KEY
                        REFERENCES minecraft_resource_exchange_claims(exchange_id),
                    level_exchange_id INTEGER NOT NULL,
                    account_id INTEGER NOT NULL
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    player_name TEXT NOT NULL,
                    item_id TEXT NOT NULL
                        CHECK (item_id IN ('minecraft:diamond', 'minecraft:emerald')),
                    item_name TEXT NOT NULL,
                    item_count INTEGER NOT NULL CHECK (item_count > 0),
                    cost_xp INTEGER NOT NULL CHECK (cost_xp > 0),
                    claim_token TEXT NOT NULL,
                    reward_applied INTEGER NOT NULL DEFAULT 0
                        CHECK (reward_applied IN (0, 1)),
                    level_completed INTEGER NOT NULL DEFAULT 0
                        CHECK (level_completed IN (0, 1)),
                    minecraft_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_notified IN (0, 1)),
                    minecraft_public_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_public_notified IN (0, 1)),
                    discord_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (discord_notified IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS minecraft_item_gacha_draws (
                    draw_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    discord_user_id INTEGER NOT NULL,
                    account_id INTEGER NOT NULL
                        REFERENCES minecraft_accounts(id) ON DELETE RESTRICT,
                    player_name TEXT NOT NULL,
                    draw_day TEXT NOT NULL,
                    draw_number INTEGER NOT NULL CHECK (draw_number BETWEEN 1 AND 3),
                    draw_kind TEXT NOT NULL CHECK (draw_kind IN ('normal', 'premium')),
                    draw_category TEXT NOT NULL DEFAULT 'all'
                        CHECK (draw_category IN ('all', 'resources', 'adventure', 'equipment')),
                    cost_xp INTEGER NOT NULL CHECK (cost_xp IN (100, 1000)),
                    tier TEXT NOT NULL
                        CHECK (tier IN ('N', 'R', 'SR', 'SSR', 'UR', 'MYTHIC')),
                    reward_key TEXT NOT NULL,
                    item_spec TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    item_count INTEGER NOT NULL CHECK (item_count BETWEEN 1 AND 128),
                    status TEXT NOT NULL DEFAULT 'reserved'
                        CHECK (status IN ('reserved', 'retryable', 'delivered', 'ambiguous')),
                    minecraft_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_notified IN (0, 1)),
                    discord_notified INTEGER NOT NULL DEFAULT 0
                        CHECK (discord_notified IN (0, 1)),
                    minecraft_notification_attempts INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_notification_attempts >= 0),
                    discord_notification_attempts INTEGER NOT NULL DEFAULT 0
                        CHECK (discord_notification_attempts >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(guild_id, discord_user_id, draw_day, draw_number)
                );
                CREATE INDEX IF NOT EXISTS minecraft_item_gacha_pending_notifications
                    ON minecraft_item_gacha_draws(
                        status, minecraft_notified, discord_notified, created_at
                    );
                CREATE TABLE IF NOT EXISTS minecraft_fishing_combo_states (
                    account_id INTEGER PRIMARY KEY
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    catch_count INTEGER NOT NULL CHECK (catch_count >= 0),
                    combo_count INTEGER NOT NULL CHECK (combo_count >= 0),
                    last_catch_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS minecraft_fishing_combo_rewards (
                    event_id TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    catch_count INTEGER NOT NULL CHECK (catch_count > 0),
                    combo_count INTEGER NOT NULL CHECK (combo_count >= 1),
                    reward_xp INTEGER NOT NULL CHECK (reward_xp > 0),
                    observed_at TEXT NOT NULL,
                    reward_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (reward_delivered IN (0, 1)),
                    audit_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (audit_delivered IN (0, 1)),
                    minecraft_public_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_public_delivered IN (0, 1)),
                    discord_public_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (discord_public_delivered IN (0, 1)),
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, catch_count)
                );
                CREATE INDEX IF NOT EXISTS fishing_combo_rewards_delivery
                    ON minecraft_fishing_combo_rewards(reward_delivered, created_at);
                CREATE INDEX IF NOT EXISTS fishing_combo_rewards_audit
                    ON minecraft_fishing_combo_rewards(audit_delivered, created_at);
                CREATE TABLE IF NOT EXISTS minecraft_woodcutting_combo_states (
                    account_id INTEGER PRIMARY KEY
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    log_count INTEGER NOT NULL CHECK (log_count >= 0),
                    combo_count INTEGER NOT NULL CHECK (combo_count >= 0),
                    last_log_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS minecraft_woodcutting_combo_rewards (
                    event_id TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    discord_user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    log_count INTEGER NOT NULL CHECK (log_count > 0),
                    combo_count INTEGER NOT NULL CHECK (combo_count >= 1),
                    reward_xp INTEGER NOT NULL CHECK (reward_xp > 0),
                    observed_at TEXT NOT NULL,
                    reward_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (reward_delivered IN (0, 1)),
                    audit_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (audit_delivered IN (0, 1)),
                    minecraft_public_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (minecraft_public_delivered IN (0, 1)),
                    discord_public_delivered INTEGER NOT NULL DEFAULT 0
                        CHECK (discord_public_delivered IN (0, 1)),
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, log_count)
                );
                CREATE INDEX IF NOT EXISTS woodcutting_combo_rewards_delivery
                    ON minecraft_woodcutting_combo_rewards(reward_delivered, created_at);
                CREATE INDEX IF NOT EXISTS woodcutting_combo_rewards_audit
                    ON minecraft_woodcutting_combo_rewards(audit_delivered, created_at);
                CREATE TABLE IF NOT EXISTS minecraft_activity_events (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('fishing', 'woodcutting', 'experience')),
                    account_id INTEGER NOT NULL
                        REFERENCES minecraft_accounts(id) ON DELETE CASCADE,
                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS minecraft_activity_events_account
                    ON minecraft_activity_events(account_id, created_at);
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
            self._add_public_delivery_columns(
                connection,
                "minecraft_fishing_combo_rewards",
            )
            self._add_public_delivery_columns(
                connection,
                "minecraft_woodcutting_combo_rewards",
            )
            self._add_resource_exchange_notification_columns(connection)
            self._add_whitelist_retry_columns(connection)
            self._add_item_gacha_notification_attempt_columns(connection)
            self._upgrade_item_gacha_draw_table(connection)
            self._add_item_gacha_category_column(connection)
            self._upgrade_item_gacha_item_count_limit(connection)

    @staticmethod
    def _add_public_delivery_columns(connection: sqlite3.Connection, table: str) -> None:
        """Add delivery flags without replaying milestones created by older versions."""
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in ("minecraft_public_delivered", "discord_public_delivered"):
            if column in columns:
                continue
            connection.execute(
                f"""
                ALTER TABLE {table}
                ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0
                    CHECK ({column} IN (0, 1))
                """
            )
            connection.execute(f"UPDATE {table} SET {column} = 1")

    @staticmethod
    def _add_resource_exchange_notification_columns(
        connection: sqlite3.Connection,
    ) -> None:
        """Add public flags without replaying exchanges completed by older versions."""
        table = "minecraft_resource_exchange_deliveries"
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in ("minecraft_public_notified", "discord_notified"):
            if column in columns:
                continue
            connection.execute(
                f"""
                ALTER TABLE {table}
                ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0
                    CHECK ({column} IN (0, 1))
                """
            )
            connection.execute(f"UPDATE {table} SET {column} = 1")

    @staticmethod
    def _add_whitelist_retry_columns(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(minecraft_accounts)")
        }
        if "whitelist_retry_count" not in columns:
            connection.execute(
                """
                ALTER TABLE minecraft_accounts
                ADD COLUMN whitelist_retry_count INTEGER NOT NULL DEFAULT 0
                    CHECK (whitelist_retry_count >= 0)
                """
            )
        if "whitelist_last_error" not in columns:
            connection.execute(
                """
                ALTER TABLE minecraft_accounts
                ADD COLUMN whitelist_last_error TEXT
                """
            )

    @staticmethod
    def _add_item_gacha_notification_attempt_columns(
        connection: sqlite3.Connection,
    ) -> None:
        table = "minecraft_item_gacha_draws"
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in (
            "minecraft_notification_attempts",
            "discord_notification_attempts",
        ):
            if column in columns:
                continue
            connection.execute(
                f"""
                ALTER TABLE {table}
                ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0
                    CHECK ({column} >= 0)
                """
            )

    @staticmethod
    def _upgrade_item_gacha_draw_table(connection: sqlite3.Connection) -> None:
        table = "minecraft_item_gacha_draws"
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if {"draw_number", "draw_kind", "cost_xp"} <= columns:
            return
        connection.executescript(
            """
            DROP TABLE IF EXISTS minecraft_item_gacha_draws_v2;
            CREATE TABLE minecraft_item_gacha_draws_v2 (
                draw_id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                discord_user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL
                    REFERENCES minecraft_accounts(id) ON DELETE RESTRICT,
                player_name TEXT NOT NULL,
                draw_day TEXT NOT NULL,
                draw_number INTEGER NOT NULL CHECK (draw_number BETWEEN 1 AND 3),
                draw_kind TEXT NOT NULL CHECK (draw_kind IN ('normal', 'premium')),
                draw_category TEXT NOT NULL DEFAULT 'all'
                    CHECK (draw_category IN ('all', 'resources', 'adventure', 'equipment')),
                cost_xp INTEGER NOT NULL CHECK (cost_xp IN (100, 1000)),
                tier TEXT NOT NULL
                    CHECK (tier IN ('N', 'R', 'SR', 'SSR', 'UR', 'MYTHIC')),
                reward_key TEXT NOT NULL,
                item_spec TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_count INTEGER NOT NULL CHECK (item_count BETWEEN 1 AND 128),
                status TEXT NOT NULL DEFAULT 'reserved'
                    CHECK (status IN ('reserved', 'retryable', 'delivered', 'ambiguous')),
                minecraft_notified INTEGER NOT NULL DEFAULT 0
                    CHECK (minecraft_notified IN (0, 1)),
                discord_notified INTEGER NOT NULL DEFAULT 0
                    CHECK (discord_notified IN (0, 1)),
                minecraft_notification_attempts INTEGER NOT NULL DEFAULT 0
                    CHECK (minecraft_notification_attempts >= 0),
                discord_notification_attempts INTEGER NOT NULL DEFAULT 0
                    CHECK (discord_notification_attempts >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, discord_user_id, draw_day, draw_number)
            );
            INSERT INTO minecraft_item_gacha_draws_v2 (
                draw_id, guild_id, discord_user_id, account_id, player_name,
                draw_day, draw_number, draw_kind, draw_category, cost_xp, tier, reward_key,
                item_spec, item_name, item_count, status, minecraft_notified,
                discord_notified, minecraft_notification_attempts,
                discord_notification_attempts, created_at, updated_at
            )
            SELECT
                draw_id, guild_id, discord_user_id, account_id, player_name,
                draw_day, 1, 'normal', 'all', 100, tier, reward_key, item_spec,
                item_name, item_count, status, minecraft_notified,
                discord_notified, minecraft_notification_attempts,
                discord_notification_attempts, created_at, updated_at
            FROM minecraft_item_gacha_draws;
            DROP TABLE minecraft_item_gacha_draws;
            ALTER TABLE minecraft_item_gacha_draws_v2 RENAME TO minecraft_item_gacha_draws;
            CREATE INDEX minecraft_item_gacha_pending_notifications
                ON minecraft_item_gacha_draws(
                    status, minecraft_notified, discord_notified, created_at
                );
            """
        )

    @staticmethod
    def _add_item_gacha_category_column(connection: sqlite3.Connection) -> None:
        table = "minecraft_item_gacha_draws"
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "draw_category" in columns:
            return
        connection.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN draw_category TEXT NOT NULL DEFAULT 'all'
                CHECK (draw_category IN ('all', 'resources', 'adventure', 'equipment'))
            """
        )

    @staticmethod
    def _upgrade_item_gacha_item_count_limit(connection: sqlite3.Connection) -> None:
        table = "minecraft_item_gacha_draws"
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if row is None:
            raise RuntimeError("minecraft item gacha draw table is missing")
        if "item_count BETWEEN 1 AND 128" in str(row["sql"]):
            return
        connection.executescript(
            """
            DROP TABLE IF EXISTS minecraft_item_gacha_draws_v3;
            CREATE TABLE minecraft_item_gacha_draws_v3 (
                draw_id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                discord_user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL
                    REFERENCES minecraft_accounts(id) ON DELETE RESTRICT,
                player_name TEXT NOT NULL,
                draw_day TEXT NOT NULL,
                draw_number INTEGER NOT NULL CHECK (draw_number BETWEEN 1 AND 3),
                draw_kind TEXT NOT NULL CHECK (draw_kind IN ('normal', 'premium')),
                draw_category TEXT NOT NULL DEFAULT 'all'
                    CHECK (draw_category IN ('all', 'resources', 'adventure', 'equipment')),
                cost_xp INTEGER NOT NULL CHECK (cost_xp IN (100, 1000)),
                tier TEXT NOT NULL
                    CHECK (tier IN ('N', 'R', 'SR', 'SSR', 'UR', 'MYTHIC')),
                reward_key TEXT NOT NULL,
                item_spec TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_count INTEGER NOT NULL CHECK (item_count BETWEEN 1 AND 128),
                status TEXT NOT NULL DEFAULT 'reserved'
                    CHECK (status IN ('reserved', 'retryable', 'delivered', 'ambiguous')),
                minecraft_notified INTEGER NOT NULL DEFAULT 0
                    CHECK (minecraft_notified IN (0, 1)),
                discord_notified INTEGER NOT NULL DEFAULT 0
                    CHECK (discord_notified IN (0, 1)),
                minecraft_notification_attempts INTEGER NOT NULL DEFAULT 0
                    CHECK (minecraft_notification_attempts >= 0),
                discord_notification_attempts INTEGER NOT NULL DEFAULT 0
                    CHECK (discord_notification_attempts >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, discord_user_id, draw_day, draw_number)
            );
            INSERT INTO minecraft_item_gacha_draws_v3 (
                draw_id, guild_id, discord_user_id, account_id, player_name,
                draw_day, draw_number, draw_kind, draw_category, cost_xp, tier,
                reward_key, item_spec, item_name, item_count, status,
                minecraft_notified, discord_notified,
                minecraft_notification_attempts, discord_notification_attempts,
                created_at, updated_at
            )
            SELECT
                draw_id, guild_id, discord_user_id, account_id, player_name,
                draw_day, draw_number, draw_kind, draw_category, cost_xp, tier,
                reward_key, item_spec, item_name, item_count, status,
                minecraft_notified, discord_notified,
                minecraft_notification_attempts, discord_notification_attempts,
                created_at, updated_at
            FROM minecraft_item_gacha_draws;
            DROP TABLE minecraft_item_gacha_draws;
            ALTER TABLE minecraft_item_gacha_draws_v3
                RENAME TO minecraft_item_gacha_draws;
            CREATE INDEX minecraft_item_gacha_pending_notifications
                ON minecraft_item_gacha_draws(
                    status, minecraft_notified, discord_notified, created_at
                );
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
            connection.execute("BEGIN IMMEDIATE")
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
                normalized_uuid = _normalize_player_uuid(player_uuid)
                uuid_matches = (
                    connection.execute(
                        """
                        SELECT * FROM minecraft_accounts
                        WHERE player_uuid = ? COLLATE NOCASE
                        ORDER BY id
                        """,
                        (normalized_uuid,),
                    ).fetchall()
                    if normalized_uuid is not None
                    else []
                )
                name_match = connection.execute(
                    """
                    SELECT * FROM minecraft_accounts
                    WHERE server_player_name = ? COLLATE NOCASE
                    """,
                    (name,),
                ).fetchone()
                name_match_uuid = (
                    _normalize_player_uuid(name_match["player_uuid"])
                    if name_match is not None
                    else None
                )
                if (
                    normalized_uuid is not None
                    and name_match_uuid is not None
                    and name_match_uuid.casefold() != normalized_uuid.casefold()
                ):
                    raise ValueError(
                        f"Whitelistのプレイヤー名が別のUUIDの登録に一致しています: {name}"
                    )
                uuid_match_ids = {row["id"] for row in uuid_matches}
                if len(uuid_matches) > 1 or (
                    uuid_matches
                    and name_match is not None
                    and name_match["id"] not in uuid_match_ids
                ):
                    if normalized_uuid is None:
                        raise ValueError(f"WhitelistのUUIDが正しくありません: {name}")
                    target_id = self._consolidate_same_owner_uuid_rows(
                        connection,
                        uuid_matches=uuid_matches,
                        name_match=name_match,
                        edition=edition,
                        minecraft_name=minecraft_name,
                        server_player_name=name,
                        player_uuid=normalized_uuid,
                        now=now,
                    )
                    existing = connection.execute(
                        "SELECT * FROM minecraft_accounts WHERE id = ?", (target_id,)
                    ).fetchone()
                else:
                    existing = (uuid_matches[0] if uuid_matches else None) or name_match
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO minecraft_accounts (
                            edition, minecraft_name, server_player_name, player_uuid,
                            managed, source, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 0, 'legacy', 'active', ?, ?)
                        """,
                        (edition, minecraft_name, name, normalized_uuid, now, now),
                    )
                else:
                    refresh_name = existing["discord_user_id"] is None
                    desired_minecraft_name = (
                        minecraft_name if refresh_name else existing["minecraft_name"]
                    )
                    desired_server_player_name = (
                        name if refresh_name else existing["server_player_name"]
                    )
                    desired_uuid = (
                        normalized_uuid if normalized_uuid is not None else existing["player_uuid"]
                    )
                    desired_status = (
                        "active"
                        if existing["status"] == "missing" and not existing["managed"]
                        else existing["status"]
                    )
                    if not (
                        existing["edition"] == edition
                        and existing["minecraft_name"] == desired_minecraft_name
                        and existing["server_player_name"] == desired_server_player_name
                        and existing["player_uuid"] == desired_uuid
                        and existing["status"] == desired_status
                    ):
                        connection.execute(
                            """
                            UPDATE minecraft_accounts
                            SET edition = ?, minecraft_name = ?, server_player_name = ?,
                                player_uuid = ?, status = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                edition,
                                desired_minecraft_name,
                                desired_server_player_name,
                                desired_uuid,
                                desired_status,
                                now,
                                existing["id"],
                            ),
                        )
                imported += 1
        return imported

    @staticmethod
    def _consolidate_same_owner_uuid_rows(
        connection: sqlite3.Connection,
        *,
        uuid_matches: list[sqlite3.Row],
        name_match: sqlite3.Row | None,
        edition: str,
        minecraft_name: str,
        server_player_name: str,
        player_uuid: str,
        now: str,
    ) -> int:
        rows_by_id = {row["id"]: row for row in uuid_matches}
        if name_match is not None:
            name_uuid = _normalize_player_uuid(name_match["player_uuid"])
            if name_uuid is not None and name_uuid.casefold() != player_uuid.casefold():
                raise ValueError(
                    f"Whitelistのプレイヤー名が別のUUIDの登録に一致しています: {server_player_name}"
                )
            rows_by_id[name_match["id"]] = name_match
        owners = {
            int(row["discord_user_id"])
            for row in rows_by_id.values()
            if row["discord_user_id"] is not None
        }
        if len(owners) > 1:
            raise ValueError(
                "同じMinecraft UUIDが複数のDiscordユーザーに紐付いています。"
                "安全のため自動統合しません。"
            )
        target = next(
            (row for row in uuid_matches if row["discord_user_id"] is not None),
            name_match if name_match is not None else uuid_matches[0],
        )
        linked = next(
            (row for row in rows_by_id.values() if row["discord_user_id"] is not None),
            target,
        )
        statuses = {row["status"] for row in rows_by_id.values()}
        if "pending_add" in statuses:
            status = "pending_add"
        elif "active" in statuses:
            status = "active"
        elif "pending_remove" in statuses:
            status = "pending_remove"
        else:
            status = "active"
        target_id = int(target["id"])
        archived_ids = [row_id for row_id in rows_by_id if row_id != target_id]
        for archived_id in archived_ids:
            connection.execute(
                """
                UPDATE minecraft_accounts
                SET server_player_name = ?, player_uuid = NULL, status = 'missing',
                    whitelist_retry_count = 0, whitelist_last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (f"#archived:{archived_id}", now, archived_id),
            )
        connection.execute(
            """
            UPDATE minecraft_accounts
            SET edition = ?, minecraft_name = ?, server_player_name = ?, player_uuid = ?,
                discord_user_id = ?, discord_username = ?, managed = ?, source = ?,
                status = ?, created_by = COALESCE(created_by, ?),
                whitelist_retry_count = 0, whitelist_last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                edition,
                minecraft_name,
                server_player_name,
                player_uuid,
                linked["discord_user_id"],
                linked["discord_username"],
                max(int(row["managed"]) for row in rows_by_id.values()),
                linked["source"],
                status,
                linked["created_by"],
                now,
                target_id,
            ),
        )
        return target_id

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
        player_uuid: str | None = None,
    ) -> MinecraftAccount:
        now = _now()
        normalized_uuid = _normalize_player_uuid(player_uuid)
        if player_uuid is not None and normalized_uuid is None:
            raise ValueError("Minecraft UUIDが正しくありません。")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            uuid_existing = (
                connection.execute(
                    """
                    SELECT id, status, player_uuid, discord_user_id
                    FROM minecraft_accounts
                    WHERE player_uuid = ? COLLATE NOCASE
                    """,
                    (normalized_uuid,),
                ).fetchone()
                if normalized_uuid is not None
                else None
            )
            name_existing = connection.execute(
                """
                SELECT id, status, player_uuid, discord_user_id
                FROM minecraft_accounts
                WHERE server_player_name = ? COLLATE NOCASE
                """,
                (server_player_name,),
            ).fetchone()
            if (
                uuid_existing is not None
                and name_existing is not None
                and uuid_existing["id"] != name_existing["id"]
            ):
                raise ValueError(
                    "Minecraft UUIDとプレイヤー名が別々の登録に一致しています。"
                    "管理者が登録状態を確認してください。"
                )
            name_existing_uuid = (
                _normalize_player_uuid(name_existing["player_uuid"])
                if name_existing is not None
                else None
            )
            if (
                normalized_uuid is not None
                and name_existing_uuid is not None
                and name_existing_uuid.casefold() != normalized_uuid.casefold()
            ):
                raise ValueError(
                    "同じプレイヤー名が別のMinecraft UUIDで登録されています。"
                    "管理者が登録状態を確認してください。"
                )
            if (
                normalized_uuid is not None
                and uuid_existing is None
                and name_existing is not None
                and name_existing_uuid is None
                and name_existing["status"] == "missing"
            ):
                # UUID不明の履歴を、同名という理由だけで新しい本人へ引き継がない。
                # 行は削除せず退避し、外部キーで結び付いた履歴も監査用に残す。
                connection.execute(
                    """
                    UPDATE minecraft_accounts
                    SET server_player_name = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (f"#archived:{name_existing['id']}", now, name_existing["id"]),
                )
                name_existing = None
            existing = uuid_existing or name_existing
            if existing is not None:
                if existing["status"] != "missing":
                    raise ValueError("このMinecraftアカウントはすでに登録されています。")
                if normalized_uuid is not None and existing["discord_user_id"] not in {
                    None,
                    discord_user_id,
                }:
                    raise ValueError(
                        "このMinecraftアカウントは別のDiscordユーザーの履歴に"
                        "紐付いています。管理者が紐付け先修正を行ってください。"
                    )
                connection.execute(
                    """
                    UPDATE minecraft_accounts SET
                        edition = ?, minecraft_name = ?, server_player_name = ?,
                        player_uuid = COALESCE(?, player_uuid), discord_user_id = ?,
                        discord_username = ?, managed = 1, source = ?, status = ?,
                        created_by = ?, approval_message_id = NULL,
                        whitelist_retry_count = 0, whitelist_last_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        edition,
                        minecraft_name,
                        server_player_name,
                        normalized_uuid,
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
                        edition, minecraft_name, server_player_name, player_uuid,
                        discord_user_id, discord_username, managed, source, status, created_by,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        edition,
                        minecraft_name,
                        server_player_name,
                        normalized_uuid,
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

    def get_by_server_player_name(self, player_name: str) -> MinecraftAccount | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE server_player_name = ? COLLATE NOCASE
                """,
                (player_name,),
            ).fetchone()
        return _account(row) if row is not None else None

    def get_by_player_uuid(self, player_uuid: str) -> MinecraftAccount | None:
        normalized_uuid = _normalize_player_uuid(player_uuid)
        if normalized_uuid is None:
            return None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE player_uuid = ? COLLATE NOCASE
                ORDER BY id
                LIMIT 2
                """,
                (normalized_uuid,),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError(
                "同じMinecraft UUIDの登録が複数あります。管理者が登録状態を確認してください。"
            )
        return _account(rows[0]) if rows else None

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

    def list_relinkable(self, limit: int = 25) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE discord_user_id IS NOT NULL
                  AND status IN ('active', 'pending_add', 'pending_remove', 'missing')
                  AND server_player_name NOT LIKE '#archived:%'
                ORDER BY edition, minecraft_name COLLATE NOCASE
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_account(row) for row in rows]

    def list_pending_removal_corrections(self, limit: int = 25) -> list[MinecraftAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_accounts
                WHERE discord_user_id IS NOT NULL AND status = 'pending_remove'
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
                  AND whitelist_retry_count < ?
                ORDER BY updated_at
                """,
                (WHITELIST_RETRY_LIMIT,),
            ).fetchall()
        return [_account(row) for row in rows]

    def record_whitelist_retry_failure(
        self,
        account_id: int,
        *,
        expected_status: str,
        error: str,
    ) -> tuple[int, bool]:
        if expected_status not in {"pending_add", "pending_remove"}:
            raise ValueError("Whitelist retry status must be pending_add or pending_remove")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_accounts
                SET whitelist_retry_count = whitelist_retry_count + 1,
                    whitelist_last_error = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (error[:1000], _now(), account_id, expected_status),
            )
            if cursor.rowcount != 1:
                return 0, False
            row = connection.execute(
                """
                SELECT whitelist_retry_count FROM minecraft_accounts
                WHERE id = ?
                """,
                (account_id,),
            ).fetchone()
        attempts = int(row["whitelist_retry_count"]) if row is not None else 0
        return attempts, attempts >= WHITELIST_RETRY_LIMIT

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

    def reconcile_whitelist(
        self, player_profiles: list[str | tuple[str, str]]
    ) -> tuple[int, int, int]:
        names_and_uuids = [
            (profile, None) if isinstance(profile, str) else profile for profile in player_profiles
        ]
        present_names = {name.casefold() for name, _ in names_and_uuids}
        present_uuids = {
            normalized
            for _, player_uuid in names_and_uuids
            if (normalized := _normalize_player_uuid(player_uuid)) is not None
        }
        queued_adds = 0
        completed_adds = 0
        completed_removals = 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, server_player_name, player_uuid, managed, status
                FROM minecraft_accounts
                WHERE status IN ('active', 'pending_add', 'pending_remove')
                """
            ).fetchall()
            now = _now()
            for row in rows:
                player_uuid = _normalize_player_uuid(row["player_uuid"])
                is_present = (
                    player_uuid in present_uuids
                    if player_uuid is not None
                    else row["server_player_name"].casefold() in present_names
                )
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
                        SET status = ?, whitelist_retry_count = 0,
                            whitelist_last_error = NULL, updated_at = ?
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

    def record_minecraft_xp_gain(
        self,
        *,
        event_id: str,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        minecraft_xp: int,
        observed_at: str,
    ) -> MinecraftXpOutboxEvent | None:
        """Paperの自然XP獲得を重複排除してlevel-bot outboxへ記録する。"""
        if minecraft_xp <= 0:
            raise ValueError("minecraft_xp must be positive")
        normalized_event_id, _ = _validate_activity_event(
            event_id=event_id,
            account_id=account_id,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            observed_at=observed_at,
            combo_window_seconds=1,
        )
        event = MinecraftXpOutboxEvent(
            event_id=normalized_event_id,
            account_id=account_id,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            minecraft_xp=minecraft_xp,
            observed_at=observed_at,
        )
        with self._connect() as connection:
            if not _claim_activity_event(
                connection,
                event_id=normalized_event_id,
                kind="experience",
                account_id=account_id,
                observed_at=observed_at,
            ):
                return None
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

    def get_or_create_minecraft_resource_exchange_claim_token(self, exchange_id: str) -> str:
        if not exchange_id:
            raise ValueError("exchange_id must not be empty")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT claim_token FROM minecraft_resource_exchange_claims WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
            if row is not None:
                return str(row["claim_token"])
            claim_token = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO minecraft_resource_exchange_claims (
                    exchange_id, claim_token, created_at
                ) VALUES (?, ?, ?)
                """,
                (exchange_id, claim_token, _now()),
            )
            return claim_token

    def get_minecraft_resource_exchange_claim_token(self, exchange_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT claim_token FROM minecraft_resource_exchange_claims WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
        return str(row["claim_token"]) if row is not None else None

    def reserve_minecraft_resource_exchange_delivery(
        self,
        *,
        exchange_id: str,
        level_exchange_id: int,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        player_name: str,
        item_id: str,
        item_name: str,
        item_count: int,
        cost_xp: int,
        claim_token: str,
    ) -> bool:
        if (
            not exchange_id
            or level_exchange_id <= 0
            or account_id <= 0
            or discord_user_id <= 0
            or guild_id <= 0
            or not player_name
            or item_id not in {"minecraft:diamond", "minecraft:emerald"}
            or not item_name
            or not 1 <= item_count <= 64
            or cost_xp <= 0
            or not claim_token
        ):
            raise ValueError("invalid Minecraft resource exchange delivery")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM minecraft_resource_exchange_deliveries WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """
                INSERT INTO minecraft_resource_exchange_deliveries (
                    exchange_id, level_exchange_id, account_id, discord_user_id,
                    guild_id, player_name, item_id, item_name, item_count,
                    cost_xp, claim_token, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange_id,
                    level_exchange_id,
                    account_id,
                    discord_user_id,
                    guild_id,
                    player_name,
                    item_id,
                    item_name,
                    item_count,
                    cost_xp,
                    claim_token,
                    _now(),
                ),
            )
        return True

    def get_minecraft_resource_exchange_delivery(
        self, exchange_id: str
    ) -> MinecraftResourceExchangeDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM minecraft_resource_exchange_deliveries WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
        return _minecraft_resource_exchange_delivery(row) if row is not None else None

    def mark_minecraft_resource_exchange_reward_applied(self, exchange_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE minecraft_resource_exchange_deliveries SET reward_applied = 1 "
                "WHERE exchange_id = ?",
                (exchange_id,),
            )

    def mark_minecraft_resource_exchange_level_completed(self, exchange_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE minecraft_resource_exchange_deliveries SET level_completed = 1 "
                "WHERE exchange_id = ? AND reward_applied = 1",
                (exchange_id,),
            )

    def mark_minecraft_resource_exchange_notified(self, exchange_id: str, destination: str) -> None:
        columns = {
            "recipient": "minecraft_notified",
            "minecraft": "minecraft_public_notified",
            "discord": "discord_notified",
        }
        column = columns.get(destination)
        if column is None:
            raise ValueError("unknown resource exchange notification destination")
        with self._connect() as connection:
            connection.execute(
                f"UPDATE minecraft_resource_exchange_deliveries SET {column} = 1 "
                "WHERE exchange_id = ? AND level_completed = 1",
                (exchange_id,),
            )

    def list_pending_minecraft_resource_exchange_deliveries(
        self,
    ) -> list[MinecraftResourceExchangeDelivery]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_resource_exchange_deliveries
                WHERE reward_applied = 1
                  AND (
                    level_completed = 0
                    OR minecraft_notified = 0
                    OR minecraft_public_notified = 0
                    OR discord_notified = 0
                  )
                ORDER BY created_at, exchange_id
                """
            ).fetchall()
        return [_minecraft_resource_exchange_delivery(row) for row in rows]

    def release_minecraft_resource_exchange_delivery(self, exchange_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM minecraft_resource_exchange_deliveries WHERE exchange_id = ?",
                (exchange_id,),
            )

    def reserve_minecraft_item_gacha_draw(
        self,
        *,
        draw_id: str,
        guild_id: int,
        discord_user_id: int,
        account_id: int,
        player_name: str,
        draw_day: str,
        draw_kind: str,
        cost_xp: int,
        tier: str,
        reward_key: str,
        item_spec: str,
        item_name: str,
        item_count: int,
        draw_category: str = "all",
    ) -> tuple[MinecraftItemGachaDraw, bool]:
        try:
            normalized_draw_id = str(uuid.UUID(draw_id))
            normalized_day = date.fromisoformat(draw_day).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError("invalid Minecraft item gacha draw") from error
        if (
            guild_id <= 0
            or discord_user_id <= 0
            or account_id <= 0
            or not player_name
            or draw_day != normalized_day
            or draw_kind not in {"normal", "premium"}
            or draw_category not in {"all", "resources", "adventure", "equipment"}
            or cost_xp != {"normal": 100, "premium": 1_000}[draw_kind]
            or tier not in {"N", "R", "SR", "SSR", "UR", "MYTHIC"}
            or not reward_key
            or not item_spec
            or not item_name
            or not 1 <= item_count <= 128
        ):
            raise ValueError("invalid Minecraft item gacha draw")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_request = connection.execute(
                "SELECT * FROM minecraft_item_gacha_draws WHERE draw_id = ?",
                (normalized_draw_id,),
            ).fetchone()
            if existing_request is not None:
                existing_draw = _minecraft_item_gacha_draw(existing_request)
                if (
                    existing_draw.guild_id != guild_id
                    or existing_draw.discord_user_id != discord_user_id
                    or existing_draw.account_id != account_id
                    or existing_draw.draw_day != normalized_day
                    or existing_draw.draw_kind != draw_kind
                    or existing_draw.draw_category != draw_category
                    or existing_draw.cost_xp != cost_xp
                ):
                    raise ValueError("Minecraft item gacha request ID was reused")
                return existing_draw, False
            existing = connection.execute(
                """
                SELECT * FROM minecraft_item_gacha_draws
                WHERE guild_id = ? AND discord_user_id = ? AND draw_day = ?
                  AND status IN ('reserved', 'retryable')
                ORDER BY draw_number DESC
                LIMIT 1
                """,
                (guild_id, discord_user_id, normalized_day),
            ).fetchone()
            if existing is not None:
                return _minecraft_item_gacha_draw(existing), False
            daily = connection.execute(
                """
                SELECT COUNT(*) AS draw_count, COALESCE(MAX(draw_number), 0) AS last_number
                FROM minecraft_item_gacha_draws
                WHERE guild_id = ? AND discord_user_id = ? AND draw_day = ?
                """,
                (guild_id, discord_user_id, normalized_day),
            ).fetchone()
            if daily is None or int(daily["draw_count"]) >= 3:
                raise MinecraftItemGachaDailyLimitReached(
                    "Minecraft item gacha daily limit reached"
                )
            draw_number = int(daily["last_number"]) + 1
            connection.execute(
                """
                INSERT INTO minecraft_item_gacha_draws (
                    draw_id, guild_id, discord_user_id, account_id, player_name,
                    draw_day, draw_number, draw_kind, draw_category, cost_xp, tier, reward_key,
                    item_spec, item_name, item_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_draw_id,
                    guild_id,
                    discord_user_id,
                    account_id,
                    player_name,
                    normalized_day,
                    draw_number,
                    draw_kind,
                    draw_category,
                    cost_xp,
                    tier,
                    reward_key,
                    item_spec,
                    item_name,
                    item_count,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM minecraft_item_gacha_draws WHERE draw_id = ?",
                (normalized_draw_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Could not load the reserved Minecraft item gacha draw")
        return _minecraft_item_gacha_draw(row), True

    def get_minecraft_item_gacha_draw(
        self, *, guild_id: int, discord_user_id: int, draw_day: str
    ) -> MinecraftItemGachaDraw | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM minecraft_item_gacha_draws
                WHERE guild_id = ? AND discord_user_id = ? AND draw_day = ?
                ORDER BY draw_number DESC
                LIMIT 1
                """,
                (guild_id, discord_user_id, draw_day),
            ).fetchone()
        return _minecraft_item_gacha_draw(row) if row is not None else None

    def count_minecraft_item_gacha_draws(
        self, *, guild_id: int, discord_user_id: int, draw_day: str
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS draw_count FROM minecraft_item_gacha_draws
                WHERE guild_id = ? AND discord_user_id = ? AND draw_day = ?
                """,
                (guild_id, discord_user_id, draw_day),
            ).fetchone()
        return int(row["draw_count"])

    def mark_minecraft_item_gacha_status(self, draw_id: str, status: str) -> None:
        transitions = {
            "reserved": {"retryable", "delivered", "ambiguous"},
            "retryable": {"reserved"},
        }
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM minecraft_item_gacha_draws WHERE draw_id = ?",
                (draw_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Minecraft item gacha draw was not found")
            current = str(row["status"])
            if status == current:
                return
            if status not in transitions.get(current, set()):
                raise ValueError(f"invalid Minecraft item gacha transition: {current} -> {status}")
            connection.execute(
                """
                UPDATE minecraft_item_gacha_draws
                SET status = ?, updated_at = ?
                WHERE draw_id = ? AND status = ?
                """,
                (status, _now(), draw_id, current),
            )

    def mark_minecraft_item_gacha_notified(self, draw_id: str, destination: str) -> None:
        column = {
            "minecraft": "minecraft_notified",
            "discord": "discord_notified",
        }.get(destination)
        if column is None:
            raise ValueError("unknown item gacha notification destination")
        with self._connect() as connection:
            connection.execute(
                f"UPDATE minecraft_item_gacha_draws SET {column} = 1, updated_at = ? "
                "WHERE draw_id = ? AND status = 'delivered'",
                (_now(), draw_id),
            )

    def begin_minecraft_item_gacha_notification_attempt(
        self,
        draw_id: str,
        destination: str,
    ) -> int | None:
        columns = {
            "minecraft": (
                "minecraft_notified",
                "minecraft_notification_attempts",
            ),
            "discord": (
                "discord_notified",
                "discord_notification_attempts",
            ),
        }.get(destination)
        if columns is None:
            raise ValueError("unknown item gacha notification destination")
        notified_column, attempts_column = columns
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT status, {notified_column}, {attempts_column}
                FROM minecraft_item_gacha_draws
                WHERE draw_id = ?
                """,
                (draw_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Minecraft item gacha draw was not found")
            attempts = int(row[attempts_column])
            if (
                str(row["status"]) != "delivered"
                or bool(row[notified_column])
                or attempts >= ITEM_GACHA_NOTIFICATION_RETRY_LIMIT
            ):
                return None
            attempts += 1
            connection.execute(
                f"""
                UPDATE minecraft_item_gacha_draws
                SET {attempts_column} = ?, updated_at = ?
                WHERE draw_id = ?
                """,
                (attempts, _now(), draw_id),
            )
        return attempts

    def has_pending_minecraft_item_gacha_notifications(self, *, guild_id: int) -> bool:
        if guild_id <= 0:
            raise ValueError("guild_id must be positive")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM minecraft_item_gacha_draws
                    WHERE guild_id = ?
                      AND status = 'delivered'
                      AND (
                          (minecraft_notified = 0
                           AND minecraft_notification_attempts < ?)
                          OR
                          (discord_notified = 0
                           AND discord_notification_attempts < ?)
                      )
                ) AS pending
                """,
                (
                    guild_id,
                    ITEM_GACHA_NOTIFICATION_RETRY_LIMIT,
                    ITEM_GACHA_NOTIFICATION_RETRY_LIMIT,
                ),
            ).fetchone()
        return bool(row["pending"])

    def list_pending_minecraft_item_gacha_notifications(
        self,
    ) -> list[MinecraftItemGachaDraw]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_item_gacha_draws
                WHERE status = 'delivered'
                  AND (
                      (minecraft_notified = 0
                       AND minecraft_notification_attempts < ?)
                      OR
                      (discord_notified = 0
                       AND discord_notification_attempts < ?)
                  )
                ORDER BY created_at, draw_id
                """,
                (
                    ITEM_GACHA_NOTIFICATION_RETRY_LIMIT,
                    ITEM_GACHA_NOTIFICATION_RETRY_LIMIT,
                ),
            ).fetchall()
        return [_minecraft_item_gacha_draw(row) for row in rows]

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
            # XPイベントは高頻度なので、永続的な重複排除は同じevent_idを保持する
            # level-botへ委ね、ローカルの受信記録はoutbox完了と同時に解放する。
            connection.execute(
                """
                DELETE FROM minecraft_activity_events
                WHERE event_id = ? AND kind = 'experience'
                """,
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

    def observe_fishing_catches(
        self,
        *,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        catch_count: int,
        observed_at: str,
        combo_window_seconds: int,
    ) -> list[FishingComboRewardEvent]:
        """釣果差分をコンボへ変換し、報酬イベントを同一transactionで作る。"""
        from mc_bot.fishing import fishing_reward_xp

        if (
            account_id <= 0
            or discord_user_id <= 0
            or guild_id <= 0
            or catch_count < 0
            or combo_window_seconds <= 0
        ):
            raise ValueError("invalid fishing combo observation")
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")

        rewards: list[FishingComboRewardEvent] = []
        with self._connect() as connection:
            state = connection.execute(
                """
                SELECT catch_count, combo_count, last_catch_at
                FROM minecraft_fishing_combo_states
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            if state is None or catch_count < int(state["catch_count"]):
                connection.execute(
                    """
                    INSERT INTO minecraft_fishing_combo_states (
                        account_id, catch_count, combo_count, last_catch_at, updated_at
                    ) VALUES (?, ?, 0, NULL, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        catch_count = excluded.catch_count,
                        combo_count = 0,
                        last_catch_at = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (account_id, catch_count, observed_at),
                )
                return []

            previous_count = int(state["catch_count"])
            last_catch_text = state["last_catch_at"]
            combo_count = int(state["combo_count"])
            combo_active = False
            if last_catch_text is not None:
                last_catch = datetime.fromisoformat(str(last_catch_text))
                elapsed = (observed - last_catch).total_seconds()
                combo_active = 0 <= elapsed <= combo_window_seconds

            if catch_count == previous_count:
                if last_catch_text is not None and not combo_active:
                    connection.execute(
                        """
                        UPDATE minecraft_fishing_combo_states
                        SET combo_count = 0, last_catch_at = NULL, updated_at = ?
                        WHERE account_id = ?
                        """,
                        (observed_at, account_id),
                    )
                return []

            if not combo_active:
                combo_count = 0
            for absolute_catch in range(previous_count + 1, catch_count + 1):
                combo_count += 1
                reward_xp = fishing_reward_xp(combo_count)
                if reward_xp <= 0:
                    continue
                event = FishingComboRewardEvent(
                    event_id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"mc-bot:fishing:{account_id}:{absolute_catch}",
                        )
                    ),
                    account_id=account_id,
                    discord_user_id=discord_user_id,
                    guild_id=guild_id,
                    catch_count=absolute_catch,
                    combo_count=combo_count,
                    reward_xp=reward_xp,
                    observed_at=observed_at,
                    reward_delivered=False,
                    audit_delivered=False,
                )
                cursor = connection.execute(
                    """
                    INSERT INTO minecraft_fishing_combo_rewards (
                        event_id, account_id, discord_user_id, guild_id,
                        catch_count, combo_count, reward_xp, observed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (
                        event.event_id,
                        event.account_id,
                        event.discord_user_id,
                        event.guild_id,
                        event.catch_count,
                        event.combo_count,
                        event.reward_xp,
                        event.observed_at,
                        _now(),
                    ),
                )
                if cursor.rowcount == 1:
                    rewards.append(event)

            connection.execute(
                """
                UPDATE minecraft_fishing_combo_states
                SET catch_count = ?, combo_count = ?, last_catch_at = ?, updated_at = ?
                WHERE account_id = ?
                """,
                (catch_count, combo_count, observed_at, observed_at, account_id),
            )
        return rewards

    def record_fishing_catch(
        self,
        *,
        event_id: str,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        observed_at: str,
        combo_window_seconds: int,
    ) -> FishingComboRewardEvent | None:
        """Paperの釣りイベントを重複排除してコンボ報酬へ変換する。"""
        from mc_bot.fishing import fishing_reward_xp

        normalized_event_id, observed = _validate_activity_event(
            event_id=event_id,
            account_id=account_id,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            observed_at=observed_at,
            combo_window_seconds=combo_window_seconds,
        )
        with self._connect() as connection:
            if not _claim_activity_event(
                connection,
                event_id=normalized_event_id,
                kind="fishing",
                account_id=account_id,
                observed_at=observed_at,
            ):
                return None
            state = connection.execute(
                """
                SELECT catch_count, combo_count, last_catch_at
                FROM minecraft_fishing_combo_states
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            catch_count = int(state["catch_count"]) + 1 if state is not None else 1
            combo_count = int(state["combo_count"]) if state is not None else 0
            last_catch_text = state["last_catch_at"] if state is not None else None
            if not _combo_is_active(last_catch_text, observed, combo_window_seconds):
                combo_count = 0
            combo_count += 1
            reward = FishingComboRewardEvent(
                event_id=normalized_event_id,
                account_id=account_id,
                discord_user_id=discord_user_id,
                guild_id=guild_id,
                catch_count=catch_count,
                combo_count=combo_count,
                reward_xp=fishing_reward_xp(combo_count),
                observed_at=observed_at,
                reward_delivered=False,
                audit_delivered=False,
            )
            connection.execute(
                """
                INSERT INTO minecraft_fishing_combo_rewards (
                    event_id, account_id, discord_user_id, guild_id,
                    catch_count, combo_count, reward_xp, observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reward.event_id,
                    reward.account_id,
                    reward.discord_user_id,
                    reward.guild_id,
                    reward.catch_count,
                    reward.combo_count,
                    reward.reward_xp,
                    reward.observed_at,
                    _now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO minecraft_fishing_combo_states (
                    account_id, catch_count, combo_count, last_catch_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    catch_count = excluded.catch_count,
                    combo_count = excluded.combo_count,
                    last_catch_at = excluded.last_catch_at,
                    updated_at = excluded.updated_at
                """,
                (account_id, catch_count, combo_count, observed_at, observed_at),
            )
        return reward

    def list_pending_fishing_reward_deliveries(
        self, account_id: int | None = None
    ) -> list[FishingComboRewardEvent]:
        query = """
            SELECT * FROM minecraft_fishing_combo_rewards
            WHERE reward_delivered = 0
        """
        parameters: tuple[int, ...] = ()
        if account_id is not None:
            query += " AND account_id = ?"
            parameters = (account_id,)
        query += " ORDER BY created_at, event_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_fishing_combo_reward(row) for row in rows]

    def list_pending_fishing_audits(self) -> list[FishingComboRewardEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_fishing_combo_rewards
                WHERE reward_delivered = 1 AND audit_delivered = 0
                ORDER BY created_at, event_id
                """
            ).fetchall()
        return [_fishing_combo_reward(row) for row in rows]

    def list_pending_fishing_public_deliveries(self) -> list[FishingComboRewardEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_fishing_combo_rewards
                WHERE reward_delivered = 1
                  AND combo_count >= 10 AND combo_count % 10 = 0
                  AND (minecraft_public_delivered = 0 OR discord_public_delivered = 0)
                ORDER BY created_at, event_id
                """
            ).fetchall()
        return [_fishing_combo_reward(row) for row in rows]

    def mark_fishing_public_delivered(self, event_id: str, destination: str) -> None:
        self._mark_combo_public_delivered(
            table="minecraft_fishing_combo_rewards",
            event_id=event_id,
            destination=destination,
        )

    def reserve_fishing_reward_delivery(
        self,
        *,
        event_id: str,
        account_id: int,
        reward_xp: int,
        observed_at: str,
    ) -> bool:
        """釣り報酬を予約し、自己付与分を通常XP観測の基準へ先行反映する。"""
        if reward_xp <= 0:
            raise ValueError("reward_xp must be positive")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_fishing_combo_rewards
                SET reward_delivered = 1
                WHERE event_id = ? AND account_id = ? AND reward_xp = ?
                  AND reward_delivered = 0
                """,
                (event_id, account_id, reward_xp),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    """
                    SELECT reward_delivered FROM minecraft_fishing_combo_rewards
                    WHERE event_id = ? AND account_id = ?
                    """,
                    (event_id, account_id),
                ).fetchone()
                if row is not None and row["reward_delivered"]:
                    return False
                raise ValueError("釣りコンボ報酬イベントが見つかりません。")
            connection.execute(
                """
                UPDATE minecraft_xp_observations
                SET current_xp = current_xp + ?, observed_at = ?
                WHERE account_id = ?
                """,
                (reward_xp, observed_at, account_id),
            )
        return True

    def release_fishing_reward_delivery(
        self, *, event_id: str, account_id: int, reward_xp: int
    ) -> None:
        """RCONが明示的に失敗した場合だけ報酬予約とXP観測基準を戻す。"""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_fishing_combo_rewards
                SET reward_delivered = 0
                WHERE event_id = ? AND account_id = ? AND reward_delivered = 1
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

    def mark_fishing_audit_delivered(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_fishing_combo_rewards
                SET audit_delivered = 1
                WHERE event_id = ? AND reward_delivered = 1
                """,
                (event_id,),
            )

    def observe_woodcutting_logs(
        self,
        *,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        log_count: int,
        observed_at: str,
        combo_window_seconds: int,
    ) -> list[WoodcuttingComboRewardEvent]:
        """原木採掘数の差分を連続伐採へ変換し、節目の報酬だけを保存する。"""
        from mc_bot.woodcutting import woodcutting_reward_xp

        if min(account_id, discord_user_id, guild_id, combo_window_seconds) <= 0 or log_count < 0:
            raise ValueError("invalid woodcutting combo observation")
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")

        rewards: list[WoodcuttingComboRewardEvent] = []
        with self._connect() as connection:
            state = connection.execute(
                """
                SELECT log_count, combo_count, last_log_at
                FROM minecraft_woodcutting_combo_states WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            if state is None or log_count < int(state["log_count"]):
                connection.execute(
                    """
                    INSERT INTO minecraft_woodcutting_combo_states (
                        account_id, log_count, combo_count, last_log_at, updated_at
                    ) VALUES (?, ?, 0, NULL, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        log_count = excluded.log_count, combo_count = 0,
                        last_log_at = NULL, updated_at = excluded.updated_at
                    """,
                    (account_id, log_count, observed_at),
                )
                return []

            previous_count = int(state["log_count"])
            combo_count = int(state["combo_count"])
            last_log_text = state["last_log_at"]
            combo_active = False
            if last_log_text is not None:
                elapsed = (observed - datetime.fromisoformat(str(last_log_text))).total_seconds()
                combo_active = 0 <= elapsed <= combo_window_seconds
            if log_count == previous_count:
                if last_log_text is not None and not combo_active:
                    connection.execute(
                        """
                        UPDATE minecraft_woodcutting_combo_states
                        SET combo_count = 0, last_log_at = NULL, updated_at = ?
                        WHERE account_id = ?
                        """,
                        (observed_at, account_id),
                    )
                return []
            if not combo_active:
                combo_count = 0

            for absolute_log in range(previous_count + 1, log_count + 1):
                combo_count += 1
                reward_xp = woodcutting_reward_xp(combo_count)
                if reward_xp <= 0:
                    continue
                event = WoodcuttingComboRewardEvent(
                    event_id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"mc-bot:woodcutting:{account_id}:{absolute_log}",
                        )
                    ),
                    account_id=account_id,
                    discord_user_id=discord_user_id,
                    guild_id=guild_id,
                    log_count=absolute_log,
                    combo_count=combo_count,
                    reward_xp=reward_xp,
                    observed_at=observed_at,
                    reward_delivered=False,
                    audit_delivered=False,
                )
                cursor = connection.execute(
                    """
                    INSERT INTO minecraft_woodcutting_combo_rewards (
                        event_id, account_id, discord_user_id, guild_id,
                        log_count, combo_count, reward_xp, observed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (
                        event.event_id,
                        event.account_id,
                        event.discord_user_id,
                        event.guild_id,
                        event.log_count,
                        event.combo_count,
                        event.reward_xp,
                        event.observed_at,
                        _now(),
                    ),
                )
                if cursor.rowcount == 1:
                    rewards.append(event)
            connection.execute(
                """
                UPDATE minecraft_woodcutting_combo_states
                SET log_count = ?, combo_count = ?, last_log_at = ?, updated_at = ?
                WHERE account_id = ?
                """,
                (log_count, combo_count, observed_at, observed_at, account_id),
            )
        return rewards

    def record_woodcutting_log(
        self,
        *,
        event_id: str,
        account_id: int,
        discord_user_id: int,
        guild_id: int,
        observed_at: str,
        combo_window_seconds: int,
    ) -> WoodcuttingComboRewardEvent | None:
        """Paperの伐採イベントを重複排除して節目の報酬へ変換する。"""
        from mc_bot.woodcutting import woodcutting_reward_xp

        normalized_event_id, observed = _validate_activity_event(
            event_id=event_id,
            account_id=account_id,
            discord_user_id=discord_user_id,
            guild_id=guild_id,
            observed_at=observed_at,
            combo_window_seconds=combo_window_seconds,
        )
        with self._connect() as connection:
            if not _claim_activity_event(
                connection,
                event_id=normalized_event_id,
                kind="woodcutting",
                account_id=account_id,
                observed_at=observed_at,
            ):
                return None
            state = connection.execute(
                """
                SELECT log_count, combo_count, last_log_at
                FROM minecraft_woodcutting_combo_states
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            log_count = int(state["log_count"]) + 1 if state is not None else 1
            combo_count = int(state["combo_count"]) if state is not None else 0
            last_log_text = state["last_log_at"] if state is not None else None
            if not _combo_is_active(last_log_text, observed, combo_window_seconds):
                combo_count = 0
            combo_count += 1
            reward_xp = woodcutting_reward_xp(combo_count)
            reward = (
                WoodcuttingComboRewardEvent(
                    event_id=normalized_event_id,
                    account_id=account_id,
                    discord_user_id=discord_user_id,
                    guild_id=guild_id,
                    log_count=log_count,
                    combo_count=combo_count,
                    reward_xp=reward_xp,
                    observed_at=observed_at,
                    reward_delivered=False,
                    audit_delivered=False,
                )
                if reward_xp > 0
                else None
            )
            if reward is not None:
                connection.execute(
                    """
                    INSERT INTO minecraft_woodcutting_combo_rewards (
                        event_id, account_id, discord_user_id, guild_id,
                        log_count, combo_count, reward_xp, observed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reward.event_id,
                        reward.account_id,
                        reward.discord_user_id,
                        reward.guild_id,
                        reward.log_count,
                        reward.combo_count,
                        reward.reward_xp,
                        reward.observed_at,
                        _now(),
                    ),
                )
            connection.execute(
                """
                INSERT INTO minecraft_woodcutting_combo_states (
                    account_id, log_count, combo_count, last_log_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    log_count = excluded.log_count,
                    combo_count = excluded.combo_count,
                    last_log_at = excluded.last_log_at,
                    updated_at = excluded.updated_at
                """,
                (account_id, log_count, combo_count, observed_at, observed_at),
            )
        return reward

    def list_pending_woodcutting_reward_deliveries(
        self, account_id: int | None = None
    ) -> list[WoodcuttingComboRewardEvent]:
        query = """
            SELECT * FROM minecraft_woodcutting_combo_rewards
            WHERE reward_delivered = 0
        """
        parameters: tuple[int, ...] = ()
        if account_id is not None:
            query += " AND account_id = ?"
            parameters = (account_id,)
        query += " ORDER BY created_at, event_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_woodcutting_combo_reward(row) for row in rows]

    def list_pending_woodcutting_audits(self) -> list[WoodcuttingComboRewardEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_woodcutting_combo_rewards
                WHERE reward_delivered = 1 AND audit_delivered = 0
                ORDER BY created_at, event_id
                """
            ).fetchall()
        return [_woodcutting_combo_reward(row) for row in rows]

    def list_pending_woodcutting_public_deliveries(
        self,
    ) -> list[WoodcuttingComboRewardEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM minecraft_woodcutting_combo_rewards
                WHERE reward_delivered = 1
                  AND (combo_count = 20 OR (combo_count >= 50 AND combo_count % 50 = 0))
                  AND (minecraft_public_delivered = 0 OR discord_public_delivered = 0)
                ORDER BY created_at, event_id
                """
            ).fetchall()
        return [_woodcutting_combo_reward(row) for row in rows]

    def mark_woodcutting_public_delivered(self, event_id: str, destination: str) -> None:
        self._mark_combo_public_delivered(
            table="minecraft_woodcutting_combo_rewards",
            event_id=event_id,
            destination=destination,
        )

    def _mark_combo_public_delivered(
        self,
        *,
        table: str,
        event_id: str,
        destination: str,
    ) -> None:
        if table not in {
            "minecraft_fishing_combo_rewards",
            "minecraft_woodcutting_combo_rewards",
        }:
            raise ValueError("invalid combo reward table")
        if destination not in {"minecraft", "discord"}:
            raise ValueError("destination must be minecraft or discord")
        column = f"{destination}_public_delivered"
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE {table} SET {column} = 1
                WHERE event_id = ? AND reward_delivered = 1
                """,
                (event_id,),
            )

    def reserve_woodcutting_reward_delivery(
        self, *, event_id: str, account_id: int, reward_xp: int, observed_at: str
    ) -> bool:
        if reward_xp <= 0:
            raise ValueError("reward_xp must be positive")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_woodcutting_combo_rewards SET reward_delivered = 1
                WHERE event_id = ? AND account_id = ? AND reward_xp = ?
                  AND reward_delivered = 0
                """,
                (event_id, account_id, reward_xp),
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    """
                    SELECT reward_delivered FROM minecraft_woodcutting_combo_rewards
                    WHERE event_id = ? AND account_id = ?
                    """,
                    (event_id, account_id),
                ).fetchone()
                if row is not None and row["reward_delivered"]:
                    return False
                raise ValueError("木こりコンボ報酬イベントが見つかりません。")
            connection.execute(
                """
                UPDATE minecraft_xp_observations
                SET current_xp = current_xp + ?, observed_at = ? WHERE account_id = ?
                """,
                (reward_xp, observed_at, account_id),
            )
        return True

    def release_woodcutting_reward_delivery(
        self, *, event_id: str, account_id: int, reward_xp: int
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_woodcutting_combo_rewards SET reward_delivered = 0
                WHERE event_id = ? AND account_id = ? AND reward_delivered = 1
                """,
                (event_id, account_id),
            )
            if cursor.rowcount != 1:
                return
            connection.execute(
                """
                UPDATE minecraft_xp_observations
                SET current_xp = MAX(current_xp - ?, 0) WHERE account_id = ?
                """,
                (reward_xp, account_id),
            )

    def mark_woodcutting_audit_delivered(self, event_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_woodcutting_combo_rewards SET audit_delivered = 1
                WHERE event_id = ? AND reward_delivered = 1
                """,
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

    def reassign_discord_user(
        self,
        account_id: int,
        *,
        expected_discord_user_id: int,
        discord_user_id: int,
        discord_username: str,
        recover_pending_remove: bool = False,
    ) -> MinecraftAccount:
        if min(account_id, expected_discord_user_id, discord_user_id) <= 0:
            raise ValueError("アカウントIDとDiscordユーザーIDは正の値で指定してください。")
        allowed_statuses = (
            ("pending_remove", "missing") if recover_pending_remove else ("active", "pending_add")
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE minecraft_accounts
                SET discord_user_id = ?, discord_username = ?,
                    status = CASE
                        WHEN status IN ('pending_remove', 'missing') THEN 'pending_add'
                        ELSE status
                    END,
                    whitelist_retry_count = 0, whitelist_last_error = NULL,
                    updated_at = ?
                WHERE id = ? AND discord_user_id = ?
                  AND status IN (?, ?)
                """,
                (
                    discord_user_id,
                    discord_username,
                    _now(),
                    account_id,
                    expected_discord_user_id,
                    *allowed_statuses,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("紐付け情報がすでに変更されたか、このアカウントは修正できません。")
        account = self.get(account_id)
        if account is None:
            raise RuntimeError("Reassigned account disappeared")
        return account

    def update_status(self, account_id: int, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE minecraft_accounts
                SET status = ?, whitelist_retry_count = 0,
                    whitelist_last_error = NULL, updated_at = ?
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
        normalized_uuid = _normalize_player_uuid(player_uuid)
        if normalized_uuid is None:
            raise ValueError("Minecraft UUIDが正しくありません。")
        with self._connect() as connection:
            current = connection.execute(
                "SELECT player_uuid FROM minecraft_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if current is None:
                return
            if _normalize_player_uuid(current["player_uuid"]) == normalized_uuid:
                return

            connection.execute("BEGIN IMMEDIATE")
            conflict = connection.execute(
                """
                SELECT id FROM minecraft_accounts
                WHERE player_uuid = ? COLLATE NOCASE AND id != ?
                LIMIT 1
                """,
                (normalized_uuid, account_id),
            ).fetchone()
            if conflict is not None:
                raise ValueError("このMinecraft UUIDはすでに登録されています。")
            connection.execute(
                """
                UPDATE minecraft_accounts
                SET player_uuid = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized_uuid, _now(), account_id),
            )

    def update_player_profile(
        self,
        account_id: int,
        *,
        minecraft_name: str,
        server_player_name: str,
        player_uuid: str,
        status: str | None = None,
    ) -> MinecraftAccount:
        normalized_uuid = _normalize_player_uuid(player_uuid)
        if normalized_uuid is None:
            raise ValueError("Minecraft UUIDが正しくありません。")
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM minecraft_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if current is None:
                raise ValueError("Minecraftアカウントが見つかりません。")
            profile_unchanged = (
                current["minecraft_name"] == minecraft_name
                and current["server_player_name"] == server_player_name
                and _normalize_player_uuid(current["player_uuid"]) == normalized_uuid
            )
            status_unchanged = status is None or (
                current["status"] == status
                and current["whitelist_retry_count"] == 0
                and current["whitelist_last_error"] is None
            )
            if profile_unchanged and status_unchanged:
                return _account(current)
            connection.execute("BEGIN IMMEDIATE")
            uuid_conflict = connection.execute(
                """
                SELECT id FROM minecraft_accounts
                WHERE player_uuid = ? COLLATE NOCASE AND id != ?
                LIMIT 1
                """,
                (normalized_uuid, account_id),
            ).fetchone()
            name_conflict = connection.execute(
                """
                SELECT id FROM minecraft_accounts
                WHERE server_player_name = ? COLLATE NOCASE AND id != ?
                LIMIT 1
                """,
                (server_player_name, account_id),
            ).fetchone()
            if uuid_conflict is not None or name_conflict is not None:
                raise ValueError(
                    "UUIDまたはプレイヤー名が別の登録で使用されています。"
                    "管理者が登録状態を確認してください。"
                )
            if status is None:
                cursor = connection.execute(
                    """
                    UPDATE minecraft_accounts
                    SET minecraft_name = ?, server_player_name = ?, player_uuid = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (minecraft_name, server_player_name, normalized_uuid, _now(), account_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE minecraft_accounts
                    SET minecraft_name = ?, server_player_name = ?, player_uuid = ?,
                        status = ?, whitelist_retry_count = 0,
                        whitelist_last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        minecraft_name,
                        server_player_name,
                        normalized_uuid,
                        status,
                        _now(),
                        account_id,
                    ),
                )
            if cursor.rowcount != 1:
                raise ValueError("Minecraftアカウントが見つかりません。")
        account = self.get(account_id)
        if account is None:
            raise RuntimeError("Updated Minecraft account disappeared")
        return account

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
        whitelist_retry_count=row["whitelist_retry_count"],
        whitelist_last_error=row["whitelist_last_error"],
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


def _minecraft_resource_exchange_delivery(
    row: sqlite3.Row,
) -> MinecraftResourceExchangeDelivery:
    return MinecraftResourceExchangeDelivery(
        exchange_id=str(row["exchange_id"]),
        level_exchange_id=int(row["level_exchange_id"]),
        account_id=int(row["account_id"]),
        discord_user_id=int(row["discord_user_id"]),
        guild_id=int(row["guild_id"]),
        player_name=str(row["player_name"]),
        item_id=str(row["item_id"]),
        item_name=str(row["item_name"]),
        item_count=int(row["item_count"]),
        cost_xp=int(row["cost_xp"]),
        claim_token=str(row["claim_token"]),
        reward_applied=bool(row["reward_applied"]),
        level_completed=bool(row["level_completed"]),
        minecraft_notified=bool(row["minecraft_notified"]),
        minecraft_public_notified=bool(row["minecraft_public_notified"]),
        discord_notified=bool(row["discord_notified"]),
    )


def _minecraft_item_gacha_draw(row: sqlite3.Row) -> MinecraftItemGachaDraw:
    return MinecraftItemGachaDraw(
        draw_id=str(row["draw_id"]),
        guild_id=int(row["guild_id"]),
        discord_user_id=int(row["discord_user_id"]),
        account_id=int(row["account_id"]),
        player_name=str(row["player_name"]),
        draw_day=str(row["draw_day"]),
        draw_number=int(row["draw_number"]),
        draw_kind=str(row["draw_kind"]),
        draw_category=str(row["draw_category"]),
        cost_xp=int(row["cost_xp"]),
        tier=str(row["tier"]),
        reward_key=str(row["reward_key"]),
        item_spec=str(row["item_spec"]),
        item_name=str(row["item_name"]),
        item_count=int(row["item_count"]),
        status=str(row["status"]),
        minecraft_notified=bool(row["minecraft_notified"]),
        discord_notified=bool(row["discord_notified"]),
        minecraft_notification_attempts=int(row["minecraft_notification_attempts"]),
        discord_notification_attempts=int(row["discord_notification_attempts"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _fishing_combo_reward(row: sqlite3.Row) -> FishingComboRewardEvent:
    return FishingComboRewardEvent(
        event_id=str(row["event_id"]),
        account_id=int(row["account_id"]),
        discord_user_id=int(row["discord_user_id"]),
        guild_id=int(row["guild_id"]),
        catch_count=int(row["catch_count"]),
        combo_count=int(row["combo_count"]),
        reward_xp=int(row["reward_xp"]),
        observed_at=str(row["observed_at"]),
        reward_delivered=bool(row["reward_delivered"]),
        audit_delivered=bool(row["audit_delivered"]),
        minecraft_public_delivered=bool(row["minecraft_public_delivered"]),
        discord_public_delivered=bool(row["discord_public_delivered"]),
    )


def _woodcutting_combo_reward(row: sqlite3.Row) -> WoodcuttingComboRewardEvent:
    return WoodcuttingComboRewardEvent(
        event_id=str(row["event_id"]),
        account_id=int(row["account_id"]),
        discord_user_id=int(row["discord_user_id"]),
        guild_id=int(row["guild_id"]),
        log_count=int(row["log_count"]),
        combo_count=int(row["combo_count"]),
        reward_xp=int(row["reward_xp"]),
        observed_at=str(row["observed_at"]),
        reward_delivered=bool(row["reward_delivered"]),
        audit_delivered=bool(row["audit_delivered"]),
        minecraft_public_delivered=bool(row["minecraft_public_delivered"]),
        discord_public_delivered=bool(row["discord_public_delivered"]),
    )


def _validate_activity_event(
    *,
    event_id: str,
    account_id: int,
    discord_user_id: int,
    guild_id: int,
    observed_at: str,
    combo_window_seconds: int,
) -> tuple[str, datetime]:
    if min(account_id, discord_user_id, guild_id, combo_window_seconds) <= 0:
        raise ValueError("invalid Minecraft activity event")
    try:
        normalized_event_id = str(uuid.UUID(event_id))
        observed = datetime.fromisoformat(observed_at)
    except ValueError as error:
        raise ValueError("invalid Minecraft activity event") from error
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return normalized_event_id, observed


def _claim_activity_event(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    kind: str,
    account_id: int,
    observed_at: str,
) -> bool:
    cursor = connection.execute(
        """
        INSERT INTO minecraft_activity_events (
            event_id, kind, account_id, observed_at, created_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO NOTHING
        """,
        (event_id, kind, account_id, observed_at, _now()),
    )
    return cursor.rowcount == 1


def _combo_is_active(
    previous_text: object,
    observed: datetime,
    combo_window_seconds: int,
) -> bool:
    if previous_text is None:
        return False
    previous = datetime.fromisoformat(str(previous_text))
    elapsed = (observed - previous).total_seconds()
    return 0 <= elapsed <= combo_window_seconds


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_player_uuid(player_uuid: object) -> str | None:
    if not isinstance(player_uuid, str) or not player_uuid.strip():
        return None
    try:
        return str(uuid.UUID(player_uuid.strip()))
    except ValueError:
        return None
