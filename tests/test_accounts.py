import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from mc_bot.accounts import WHITELIST_RETRY_LIMIT, AccountStore


def test_initialize_adds_minecraft_reward_delivery_column_to_existing_database(
    tmp_path,
) -> None:
    database = tmp_path / "accounts.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE minecraft_advancement_rewards (
                account_id INTEGER NOT NULL,
                advancement TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                discord_user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                minecraft_xp INTEGER NOT NULL CHECK (minecraft_xp > 0),
                observed_at TEXT NOT NULL,
                PRIMARY KEY (account_id, advancement)
            )
            """
        )

    AccountStore(database).initialize()

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(minecraft_advancement_rewards)")
        }
    assert "minecraft_reward_delivered" in columns


def test_whitelist_retry_migration_preserves_existing_accounts(tmp_path) -> None:
    database = tmp_path / "accounts.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE minecraft_accounts (
                id INTEGER PRIMARY KEY,
                edition TEXT NOT NULL,
                minecraft_name TEXT NOT NULL,
                server_player_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                player_uuid TEXT,
                discord_user_id INTEGER,
                discord_username TEXT,
                managed INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by INTEGER,
                approval_message_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO minecraft_accounts (
                edition, minecraft_name, server_player_name, managed, source,
                status, created_at, updated_at
            ) VALUES ('java', 'Steve', 'Steve', 1, 'self', 'pending_add', 'old', 'old')
            """
        )

    AccountStore(database).initialize()

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT minecraft_name, status, whitelist_retry_count, whitelist_last_error
            FROM minecraft_accounts WHERE server_player_name = 'Steve'
            """
        ).fetchone()
    assert row is not None
    assert tuple(row) == ("Steve", "pending_add", 0, None)


def test_item_gacha_notification_retry_migration_preserves_existing_draws(tmp_path) -> None:
    database = tmp_path / "accounts.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE minecraft_item_gacha_draws (
                draw_id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                discord_user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                draw_day TEXT NOT NULL,
                tier TEXT NOT NULL,
                reward_key TEXT NOT NULL,
                item_spec TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                minecraft_notified INTEGER NOT NULL DEFAULT 0,
                discord_notified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, discord_user_id, draw_day)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO minecraft_item_gacha_draws (
                draw_id, guild_id, discord_user_id, account_id, player_name,
                draw_day, tier, reward_key, item_spec, item_name, item_count,
                status, created_at, updated_at
            ) VALUES (
                'old-draw', 456, 123, 1, 'Steve', '2026-08-14', 'N',
                'n_iron', 'minecraft:iron_ingot', '鉄インゴット', 24,
                'delivered', 'old', 'old'
            )
            """
        )

    AccountStore(database).initialize()

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT draw_id, minecraft_notification_attempts,
                   discord_notification_attempts
            FROM minecraft_item_gacha_draws
            """
        ).fetchone()
    assert row == ("old-draw", 0, 0)


def test_whitelist_retry_failures_persist_and_stop_at_limit(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Missing",
        server_player_name="Missing",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="pending_add",
        created_by=123,
    )

    for attempt in range(1, WHITELIST_RETRY_LIMIT + 1):
        attempts, exhausted = store.record_whitelist_retry_failure(
            account.id,
            expected_status="pending_add",
            error=f"failure {attempt}",
        )
        assert attempts == attempt
        assert exhausted is (attempt == WHITELIST_RETRY_LIMIT)

    failed = store.get(account.id)
    assert failed is not None
    assert failed.status == "pending_add"
    assert failed.whitelist_retry_count == WHITELIST_RETRY_LIMIT
    assert failed.whitelist_last_error == f"failure {WHITELIST_RETRY_LIMIT}"
    assert store.list_pending_actions() == []

    store.update_status(account.id, "pending_add")

    reset = store.get(account.id)
    assert reset is not None
    assert reset.whitelist_retry_count == 0
    assert reset.whitelist_last_error is None
    assert [item.id for item in store.list_pending_actions()] == [account.id]

    store.update_status(account.id, "pending_remove")
    for _ in range(WHITELIST_RETRY_LIMIT):
        store.record_whitelist_retry_failure(
            account.id,
            expected_status="pending_remove",
            error="remove failed",
        )

    failed_remove = store.get(account.id)
    assert failed_remove is not None
    assert failed_remove.status == "pending_remove"
    assert failed_remove.whitelist_retry_count == WHITELIST_RETRY_LIMIT
    assert store.list_pending_actions() == []


def test_public_delivery_migration_does_not_replay_existing_combo_events(tmp_path) -> None:
    database = tmp_path / "accounts.db"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE minecraft_fishing_combo_rewards (
                event_id TEXT PRIMARY KEY,
                reward_delivered INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO minecraft_fishing_combo_rewards (event_id, reward_delivered)
            VALUES ('old-event', 1)
            """
        )
        AccountStore._add_public_delivery_columns(
            connection,
            "minecraft_fishing_combo_rewards",
        )
        row = connection.execute(
            """
            SELECT minecraft_public_delivered, discord_public_delivered
            FROM minecraft_fishing_combo_rewards WHERE event_id = 'old-event'
            """
        ).fetchone()
    assert row is not None
    assert tuple(row) == (1, 1)


def test_resource_notification_migration_does_not_replay_existing_exchanges(
    tmp_path,
) -> None:
    database = tmp_path / "accounts.db"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE minecraft_resource_exchange_deliveries (
                exchange_id TEXT PRIMARY KEY,
                minecraft_notified INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO minecraft_resource_exchange_deliveries (
                exchange_id, minecraft_notified
            ) VALUES ('old-exchange', 1)
            """
        )
        AccountStore._add_resource_exchange_notification_columns(connection)
        row = connection.execute(
            """
            SELECT minecraft_public_notified, discord_notified
            FROM minecraft_resource_exchange_deliveries
            WHERE exchange_id = 'old-exchange'
            """
        ).fetchone()
    assert row is not None
    assert tuple(row) == (1, 1)


def test_imports_existing_whitelist_as_protected_and_unlinked(tmp_path) -> None:
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps(
            [
                {"uuid": "java-uuid", "name": "Steve"},
                {
                    "uuid": "00000000-0000-0000-0009-123456789abc",
                    "name": ".Bedrock_User",
                },
            ]
        ),
        encoding="utf-8",
    )
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()

    store.import_whitelist(whitelist)

    accounts = store.list_unlinked()
    assert [(account.edition, account.minecraft_name) for account in accounts] == [
        ("bedrock", "Bedrock_User"),
        ("java", "Steve"),
    ]
    assert all(not account.managed for account in accounts)
    assert all(account.status == "active" for account in accounts)


def test_repeated_whitelist_import_does_not_rewrite_unchanged_account(tmp_path) -> None:
    database = tmp_path / "accounts.db"
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps(
            [
                {
                    "uuid": "8667ba71-b85a-4004-af54-457a9734eed7",
                    "name": "Steve",
                }
            ]
        ),
        encoding="utf-8",
    )
    store = AccountStore(database)
    store.initialize()
    store.import_whitelist(whitelist)
    account = store.list_unlinked()[0]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE minecraft_accounts SET updated_at = 'unchanged' WHERE id = ?",
            (account.id,),
        )

    store.import_whitelist(whitelist)

    with sqlite3.connect(database) as connection:
        updated_at = connection.execute(
            "SELECT updated_at FROM minecraft_accounts WHERE id = ?",
            (account.id,),
        ).fetchone()
    assert updated_at == ("unchanged",)


def test_whitelist_import_does_not_duplicate_linked_player_with_same_uuid(tmp_path) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps([{"uuid": player_uuid, "name": ".BuckedAtol84031"}]),
        encoding="utf-8",
    )
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    store.import_whitelist(whitelist)
    original = store.list_unlinked()[0]
    store.link_existing(
        original.id,
        discord_user_id=123,
        discord_username="user",
        managed=True,
        created_by=999,
    )

    whitelist.write_text(
        json.dumps([{"uuid": player_uuid, "name": ".yuki19911261"}]),
        encoding="utf-8",
    )
    store.import_whitelist(whitelist)

    changed = store.get(original.id)
    assert changed is not None
    assert changed.server_player_name == ".BuckedAtol84031"
    assert changed.minecraft_name == "BuckedAtol84031"
    assert changed.player_uuid == player_uuid
    assert changed.discord_user_id == 123
    assert changed.managed
    assert len(store.list_whitelist_registrations()) == 1


def test_registration_rejects_duplicate_uuid_under_another_name(tmp_path) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    store.create_registration(
        edition="bedrock",
        minecraft_name="BuckedAtol84031",
        server_player_name=".BuckedAtol84031",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="user",
        source="self",
        status="active",
        created_by=123,
    )

    with pytest.raises(ValueError, match="すでに登録"):
        store.create_registration(
            edition="bedrock",
            minecraft_name="yuki19911261",
            server_player_name=".yuki19911261",
            player_uuid=player_uuid,
            discord_user_id=456,
            discord_username="other",
            source="self",
            status="pending_add",
            created_by=456,
        )


def test_registration_does_not_reuse_missing_name_with_different_uuid(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    old = store.create_registration(
        edition="bedrock",
        minecraft_name="SameName",
        server_player_name=".SameName",
        player_uuid="00000000-0000-0000-0009-01fb7be05000",
        discord_user_id=123,
        discord_username="old",
        source="self",
        status="active",
        created_by=123,
    )
    store.update_status(old.id, "missing")

    with pytest.raises(ValueError, match="別のMinecraft UUID"):
        store.create_registration(
            edition="bedrock",
            minecraft_name="SameName",
            server_player_name=".SameName",
            player_uuid="00000000-0000-0000-0009-01fb7be05001",
            discord_user_id=456,
            discord_username="new",
            source="self",
            status="pending_add",
            created_by=456,
        )

    unchanged = store.get(old.id)
    assert unchanged is not None
    assert unchanged.player_uuid == "00000000-0000-0000-0009-01fb7be05000"
    assert unchanged.discord_user_id == 123


def test_registration_archives_uuid_unknown_missing_name_instead_of_reusing_history(
    tmp_path,
) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    old = store.create_registration(
        edition="bedrock",
        minecraft_name="SameName",
        server_player_name=".SameName",
        discord_user_id=123,
        discord_username="old",
        source="self",
        status="active",
        created_by=123,
    )
    store.update_status(old.id, "missing")

    current = store.create_registration(
        edition="bedrock",
        minecraft_name="SameName",
        server_player_name=".SameName",
        player_uuid="00000000-0000-0000-0009-01fb7be05000",
        discord_user_id=456,
        discord_username="current",
        source="self",
        status="pending_add",
        created_by=456,
    )

    archived = store.get(old.id)
    assert archived is not None
    assert archived.status == "missing"
    assert archived.server_player_name == f"#archived:{old.id}"
    assert archived.discord_user_id == 123
    assert current.id != old.id
    assert current.server_player_name == ".SameName"
    assert current.discord_user_id == 456


def test_reconcile_uses_uuid_when_whitelist_name_changed(tmp_path) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="bedrock",
        minecraft_name="BuckedAtol84031",
        server_player_name=".BuckedAtol84031",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="user",
        source="self",
        status="active",
        created_by=123,
    )

    assert store.reconcile_whitelist([(".yuki19911261", player_uuid)]) == (0, 0, 0)
    assert store.get(account.id).status == "active"  # type: ignore[union-attr]


def test_whitelist_import_archives_same_owner_duplicate_uuid_rows(tmp_path) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    database = tmp_path / "accounts.db"
    store = AccountStore(database)
    store.initialize()
    old = store.create_registration(
        edition="bedrock",
        minecraft_name="BuckedAtol84031",
        server_player_name=".BuckedAtol84031",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="user",
        source="admin",
        status="active",
        created_by=999,
    )
    store.update_status(old.id, "pending_remove")
    current = store.create_registration(
        edition="bedrock",
        minecraft_name="yuki19911261",
        server_player_name=".yuki19911261",
        discord_user_id=123,
        discord_username="user",
        source="admin",
        status="pending_add",
        created_by=999,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE minecraft_accounts SET player_uuid = ? WHERE id = ?",
            (player_uuid, current.id),
        )
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps([{"uuid": player_uuid, "name": ".yuki19911261"}]),
        encoding="utf-8",
    )

    store.import_whitelist(whitelist)
    store.reconcile_whitelist([(".yuki19911261", player_uuid)])

    retained = store.get(old.id)
    archived = store.get(current.id)
    assert retained is not None
    assert retained.status == "active"
    assert retained.player_uuid == player_uuid
    assert retained.server_player_name == ".yuki19911261"
    assert retained.discord_user_id == 123
    assert retained.managed
    assert archived is not None
    assert archived.status == "missing"
    assert archived.player_uuid is None
    assert archived.server_player_name == f"#archived:{current.id}"
    assert [account.id for account in store.list_relinkable()] == [retained.id]


def test_whitelist_import_keeps_access_when_same_uuid_is_active_and_pending_remove(
    tmp_path,
) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    database = tmp_path / "accounts.db"
    store = AccountStore(database)
    store.initialize()
    removing = store.create_registration(
        edition="bedrock",
        minecraft_name="OldName",
        server_player_name=".OldName",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="user",
        source="admin",
        status="active",
        created_by=999,
    )
    store.update_status(removing.id, "pending_remove")
    active = store.create_registration(
        edition="bedrock",
        minecraft_name="CurrentName",
        server_player_name=".CurrentName",
        discord_user_id=123,
        discord_username="user",
        source="admin",
        status="active",
        created_by=999,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE minecraft_accounts SET player_uuid = ? WHERE id = ?",
            (player_uuid, active.id),
        )
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps([{"uuid": player_uuid, "name": ".CurrentName"}]),
        encoding="utf-8",
    )

    store.import_whitelist(whitelist)

    retained = store.get(removing.id)
    archived = store.get(active.id)
    assert retained is not None
    assert retained.status == "active"
    assert retained.server_player_name == ".CurrentName"
    assert archived is not None
    assert archived.status == "missing"


def test_whitelist_import_refuses_duplicate_uuid_with_different_owners(tmp_path) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    database = tmp_path / "accounts.db"
    store = AccountStore(database)
    store.initialize()
    first = store.create_registration(
        edition="bedrock",
        minecraft_name="FirstName",
        server_player_name=".FirstName",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="first",
        source="admin",
        status="active",
        created_by=999,
    )
    second = store.create_registration(
        edition="bedrock",
        minecraft_name="SecondName",
        server_player_name=".SecondName",
        discord_user_id=456,
        discord_username="second",
        source="admin",
        status="pending_add",
        created_by=999,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE minecraft_accounts SET player_uuid = ? WHERE id = ?",
            (player_uuid, second.id),
        )
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps([{"uuid": player_uuid, "name": ".SecondName"}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="複数のDiscordユーザー"):
        store.import_whitelist(whitelist)

    assert store.get(first.id).status == "active"  # type: ignore[union-attr]
    assert store.get(second.id).status == "pending_add"  # type: ignore[union-attr]


def test_whitelist_import_does_not_reassign_recycled_name_to_another_uuid(tmp_path) -> None:
    old_uuid = "00000000-0000-0000-0009-01fb7be05000"
    new_uuid = "00000000-0000-0000-0009-01fb7be05001"
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="bedrock",
        minecraft_name="SameName",
        server_player_name=".SameName",
        player_uuid=old_uuid,
        discord_user_id=123,
        discord_username="user",
        source="admin",
        status="active",
        created_by=999,
    )
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(json.dumps([{"uuid": new_uuid, "name": ".SameName"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="別のUUID"):
        store.import_whitelist(whitelist)

    unchanged = store.get(account.id)
    assert unchanged is not None
    assert unchanged.player_uuid == old_uuid
    assert unchanged.discord_user_id == 123


def test_unchanged_player_profile_does_not_write_database(tmp_path) -> None:
    database = tmp_path / "accounts.db"
    store = AccountStore(database)
    store.initialize()
    player_uuid = "8667ba71-b85a-4004-af54-457a9734eed7"
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE minecraft_accounts SET updated_at = 'unchanged' WHERE id = ?",
            (account.id,),
        )

    unchanged = store.update_player_profile(
        account.id,
        minecraft_name="Steve",
        server_player_name="Steve",
        player_uuid=player_uuid,
    )

    assert unchanged == store.get(account.id)
    with sqlite3.connect(database) as connection:
        updated_at = connection.execute(
            "SELECT updated_at FROM minecraft_accounts WHERE id = ?",
            (account.id,),
        ).fetchone()
    assert updated_at == ("unchanged",)


def test_links_multiple_accounts_to_one_discord_user(tmp_path) -> None:
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps(
            [
                {"uuid": "uuid-1", "name": "Steve"},
                {"uuid": "uuid-2", "name": "Alex"},
            ]
        ),
        encoding="utf-8",
    )
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    store.import_whitelist(whitelist)
    steve, alex = store.list_unlinked()

    store.link_existing(
        steve.id,
        discord_user_id=123,
        discord_username="hoge",
        managed=False,
        created_by=999,
    )
    store.link_existing(
        alex.id,
        discord_user_id=123,
        discord_username="hoge",
        managed=True,
        created_by=999,
    )

    linked = store.list_for_discord_user(123)
    assert len(linked) == 2
    assert {account.minecraft_name for account in linked} == {"Alex", "Steve"}
    assert {account.managed for account in linked} == {False, True}


def test_reassigns_discord_user_without_changing_minecraft_registration(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="wrong-user",
        source="admin",
        status="active",
        created_by=999,
    )
    store.update_player_uuid(account.id, "8667ba71-b85a-4004-af54-457a9734eed7")
    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=1001,
            current_xp=100,
            observed_at="2026-08-10T00:00:00+00:00",
        )
        is None
    )
    store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=1001,
        current_xp=110,
        observed_at="2026-08-10T00:01:00+00:00",
    )

    changed = store.reassign_discord_user(
        account.id,
        expected_discord_user_id=123,
        discord_user_id=456,
        discord_username="correct-user",
    )

    assert changed.discord_user_id == 456
    assert changed.discord_username == "correct-user"
    assert changed.player_uuid == "8667ba71-b85a-4004-af54-457a9734eed7"
    assert changed.managed
    assert changed.source == "admin"
    assert changed.status == "active"
    assert changed.created_by == 999
    assert store.list_for_discord_user(123) == []
    assert store.list_for_discord_user(456) == [changed]
    assert store.list_relinkable() == [changed]
    store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=456,
        guild_id=1001,
        current_xp=120,
        observed_at="2026-08-10T00:02:00+00:00",
    )
    assert [event.discord_user_id for event in store.list_minecraft_xp_outbox()] == [
        123,
        456,
    ]


def test_reassign_rejects_stale_discord_owner(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="first-user",
        source="admin",
        status="active",
        created_by=999,
    )
    store.reassign_discord_user(
        account.id,
        expected_discord_user_id=123,
        discord_user_id=456,
        discord_username="second-user",
    )

    try:
        store.reassign_discord_user(
            account.id,
            expected_discord_user_id=123,
            discord_user_id=789,
            discord_username="third-user",
        )
    except ValueError as error:
        assert "すでに変更" in str(error)
    else:
        raise AssertionError("expected stale owner to be rejected")

    assert store.get(account.id).discord_user_id == 456  # type: ignore[union-attr]


def test_reassign_cancels_pending_removal_only_in_recovery_mode(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="wrong-user",
        source="admin",
        status="active",
        created_by=999,
    )
    store.update_status(account.id, "pending_remove")

    assert [item.id for item in store.list_relinkable()] == [account.id]
    assert [item.id for item in store.list_pending_removal_corrections()] == [account.id]
    try:
        store.reassign_discord_user(
            account.id,
            expected_discord_user_id=123,
            discord_user_id=456,
            discord_username="correct-user",
        )
    except ValueError as error:
        assert "修正できません" in str(error)
    else:
        raise AssertionError("expected pending removal to require recovery mode")

    changed = store.reassign_discord_user(
        account.id,
        expected_discord_user_id=123,
        discord_user_id=456,
        discord_username="correct-user",
        recover_pending_remove=True,
    )

    assert changed.discord_user_id == 456
    assert changed.discord_username == "correct-user"
    assert changed.status == "pending_add"
    assert changed.managed
    assert changed.source == "admin"


def test_reassign_recovers_when_pending_removal_completed_during_confirmation(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="wrong-user",
        source="admin",
        status="active",
        created_by=999,
    )
    store.update_status(account.id, "pending_remove")
    store.update_status(account.id, "missing")

    assert [item.id for item in store.list_relinkable()] == [account.id]

    changed = store.reassign_discord_user(
        account.id,
        expected_discord_user_id=123,
        discord_user_id=456,
        discord_username="correct-user",
        recover_pending_remove=True,
    )

    assert changed.discord_user_id == 456
    assert changed.status == "pending_add"


def test_updates_resolved_player_uuid(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="pending_add",
        created_by=123,
    )

    store.update_player_uuid(account.id, "8667ba71-b85a-4004-af54-457a9734eed7")

    assert store.get(account.id).player_uuid == "8667ba71-b85a-4004-af54-457a9734eed7"  # type: ignore[union-attr]


def test_unlinking_protected_account_preserves_whitelist_record(tmp_path) -> None:
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        '[{"uuid": "uuid-1", "name": "Steve"}]',
        encoding="utf-8",
    )
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    store.import_whitelist(whitelist)
    account = store.list_unlinked()[0]
    store.link_existing(
        account.id,
        discord_user_id=123,
        discord_username="hoge",
        managed=False,
        created_by=999,
    )

    store.unlink_protected(account.id)

    preserved = store.get(account.id)
    assert preserved is not None
    assert preserved.discord_user_id is None
    assert preserved.status == "active"
    assert not preserved.managed


def test_rejects_duplicate_minecraft_account_registration(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    arguments = {
        "edition": "java",
        "minecraft_name": "Steve",
        "server_player_name": "Steve",
        "discord_user_id": 123,
        "discord_username": "hoge",
        "source": "self",
        "status": "pending_add",
        "created_by": 123,
    }
    store.create_registration(**arguments)

    try:
        store.create_registration(**arguments)
    except ValueError as error:
        assert str(error) == "このMinecraftアカウントはすでに登録されています。"
    else:
        raise AssertionError("duplicate account was accepted")


def test_allows_removed_managed_account_to_be_registered_again(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="old-user",
        source="self",
        status="pending_add",
        created_by=123,
    )
    store.update_status(account.id, "missing")

    restored = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=456,
        discord_username="new-user",
        source="self",
        status="pending_add",
        created_by=456,
    )

    assert restored.id == account.id
    assert restored.discord_user_id == 456
    assert restored.discord_username == "new-user"
    assert restored.status == "pending_add"


def test_rejects_reassigning_missing_uuid_registration_to_another_discord_user(
    tmp_path,
) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        player_uuid="8667ba71-b85a-4004-af54-457a9734eed7",
        discord_user_id=123,
        discord_username="old-user",
        source="self",
        status="pending_add",
        created_by=123,
    )
    store.update_status(account.id, "missing")

    with pytest.raises(ValueError, match="別のDiscordユーザーの履歴"):
        store.create_registration(
            edition="java",
            minecraft_name="Steve",
            server_player_name="Steve",
            player_uuid="8667ba71-b85a-4004-af54-457a9734eed7",
            discord_user_id=456,
            discord_username="new-user",
            source="self",
            status="pending_add",
            created_by=456,
        )

    unchanged = store.get(account.id)
    assert unchanged is not None
    assert unchanged.discord_user_id == 123
    assert unchanged.status == "missing"


def test_allows_same_discord_user_to_restore_missing_uuid_registration(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        player_uuid="8667ba71-b85a-4004-af54-457a9734eed7",
        discord_user_id=123,
        discord_username="old-name",
        source="self",
        status="pending_add",
        created_by=123,
    )
    store.update_status(account.id, "missing")

    restored = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        player_uuid="8667ba71-b85a-4004-af54-457a9734eed7",
        discord_user_id=123,
        discord_username="current-name",
        source="self",
        status="pending_add",
        created_by=123,
    )

    assert restored.id == account.id
    assert restored.discord_user_id == 123
    assert restored.discord_username == "current-name"
    assert restored.status == "pending_add"


def test_reconciles_database_statuses_with_actual_whitelist(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    managed_missing = store.create_registration(
        edition="java",
        minecraft_name="Missing",
        server_player_name="Missing",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="pending_add",
        created_by=123,
    )
    store.update_status(managed_missing.id, "active")
    completed_add = store.create_registration(
        edition="java",
        minecraft_name="Added",
        server_player_name="Added",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="pending_add",
        created_by=123,
    )
    completed_remove = store.create_registration(
        edition="java",
        minecraft_name="Removed",
        server_player_name="Removed",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="pending_add",
        created_by=123,
    )
    store.update_status(completed_remove.id, "pending_remove")

    changes = store.reconcile_whitelist(["Added"])

    assert changes == (1, 1, 1)
    assert store.get(managed_missing.id).status == "pending_add"  # type: ignore[union-attr]
    assert store.get(completed_add.id).status == "active"  # type: ignore[union-attr]
    assert store.get(completed_remove.id).status == "missing"  # type: ignore[union-attr]
    assert store.count_summary()[0] == 2


def test_preserves_removed_protected_whitelist_registration(tmp_path) -> None:
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text('[{"uuid": "uuid-1", "name": "Legacy"}]', encoding="utf-8")
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    store.import_whitelist(whitelist)
    account = store.list_unlinked()[0]

    assert store.reconcile_whitelist([]) == (0, 0, 0)

    updated = store.get(account.id)
    assert updated is not None
    assert updated.status == "active"


def test_persists_minecraft_xp_observation_and_outbox(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )

    baseline = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=100,
        observed_at="2026-08-02T00:00:00+00:00",
    )
    gained = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=350,
        observed_at="2026-08-02T00:00:30+00:00",
    )
    spent = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=50,
        observed_at="2026-08-02T00:01:00+00:00",
    )

    assert baseline is None
    assert gained is not None
    assert gained.minecraft_xp == 250
    assert spent is None
    assert store.list_linked_active() == [account]
    assert store.list_minecraft_xp_outbox() == [gained]

    store.mark_minecraft_xp_delivered(gained.event_id)

    assert store.list_minecraft_xp_outbox() == []


def test_doubled_minecraft_xp_is_not_observed_again(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )

    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=100,
            observed_at="2026-08-02T00:00:00+00:00",
            double_in_game_xp=True,
        )
        is None
    )
    gained = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=110,
        observed_at="2026-08-02T00:00:30+00:00",
        double_in_game_xp=True,
    )
    bonus_observed = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=120,
        observed_at="2026-08-02T00:01:00+00:00",
        double_in_game_xp=True,
    )
    gained_again = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=125,
        observed_at="2026-08-02T00:01:30+00:00",
        double_in_game_xp=True,
    )

    assert gained is not None
    assert gained.minecraft_xp == 10
    assert bonus_observed is None
    assert gained_again is not None
    assert gained_again.minecraft_xp == 5


def test_fishing_bonus_starts_at_one_and_resets_after_window(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    common = {
        "account_id": account.id,
        "discord_user_id": 123,
        "guild_id": 456,
        "combo_window_seconds": 90,
    }

    assert (
        store.observe_fishing_catches(
            **common, catch_count=0, observed_at="2026-08-09T00:00:00+00:00"
        )
        == []
    )
    first = store.observe_fishing_catches(
        **common, catch_count=1, observed_at="2026-08-09T00:00:10+00:00"
    )
    assert [(event.combo_count, event.reward_xp) for event in first] == [(1, 2)]
    second = store.observe_fishing_catches(
        **common, catch_count=2, observed_at="2026-08-09T00:01:40+00:00"
    )
    assert [(event.combo_count, event.reward_xp) for event in second] == [(2, 5)]
    assert (
        store.observe_fishing_catches(
            **common, catch_count=2, observed_at="2026-08-09T00:03:11+00:00"
        )
        == []
    )
    restarted_first = store.observe_fishing_catches(
        **common, catch_count=3, observed_at="2026-08-09T00:03:20+00:00"
    )
    assert [(event.combo_count, event.reward_xp) for event in restarted_first] == [(1, 2)]
    restarted_second = store.observe_fishing_catches(
        **common, catch_count=4, observed_at="2026-08-09T00:03:30+00:00"
    )
    assert [(event.combo_count, event.reward_xp) for event in restarted_second] == [(2, 5)]


def test_event_driven_fishing_is_idempotent_and_uses_event_timestamps(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    common = {
        "account_id": account.id,
        "discord_user_id": 123,
        "guild_id": 456,
        "combo_window_seconds": 90,
    }

    first = store.record_fishing_catch(
        **common,
        event_id="00000000-0000-0000-0000-000000000001",
        observed_at="2026-08-09T00:00:00+00:00",
    )
    replay = store.record_fishing_catch(
        **common,
        event_id="00000000-0000-0000-0000-000000000001",
        observed_at="2026-08-09T00:00:01+00:00",
    )
    second = store.record_fishing_catch(
        **common,
        event_id="00000000-0000-0000-0000-000000000002",
        observed_at="2026-08-09T00:01:30+00:00",
    )
    reset = store.record_fishing_catch(
        **common,
        event_id="00000000-0000-0000-0000-000000000003",
        observed_at="2026-08-09T00:03:01+00:00",
    )

    assert first is not None
    assert (first.catch_count, first.combo_count, first.reward_xp) == (1, 1, 2)
    assert replay is None
    assert second is not None and (second.catch_count, second.combo_count, second.reward_xp) == (
        2,
        2,
        5,
    )
    assert reset is not None and (reset.catch_count, reset.combo_count, reset.reward_xp) == (
        3,
        1,
        2,
    )


def test_natural_minecraft_xp_event_creates_idempotent_outbox_entry(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    event_id = "00000000-0000-0000-0000-000000000009"
    arguments = {
        "event_id": event_id,
        "account_id": account.id,
        "discord_user_id": 123,
        "guild_id": 456,
        "minecraft_xp": 37,
        "observed_at": "2026-08-11T00:00:00+00:00",
    }

    recorded = store.record_minecraft_xp_gain(**arguments)
    replay = store.record_minecraft_xp_gain(**arguments)

    assert recorded is not None
    assert recorded.event_id == event_id
    assert recorded.minecraft_xp == 37
    assert replay is None
    assert store.list_minecraft_xp_outbox() == [recorded]


def test_natural_xp_event_matches_legacy_positive_delta_pseudo_oracle(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    observed_at = "2026-08-11T00:00:05+00:00"
    store.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=100,
        observed_at="2026-08-11T00:00:00+00:00",
    )

    legacy = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=137,
        observed_at=observed_at,
    )
    event_driven = store.record_minecraft_xp_gain(
        event_id="00000000-0000-0000-0000-000000000010",
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        minecraft_xp=37,
        observed_at=observed_at,
    )

    assert legacy is not None
    assert event_driven is not None
    assert (
        event_driven.account_id,
        event_driven.discord_user_id,
        event_driven.guild_id,
        event_driven.minecraft_xp,
        event_driven.observed_at,
    ) == (
        legacy.account_id,
        legacy.discord_user_id,
        legacy.guild_id,
        legacy.minecraft_xp,
        legacy.observed_at,
    )


def test_fishing_combo_creates_each_missed_catch_reward_once(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    common = {
        "account_id": account.id,
        "discord_user_id": 123,
        "guild_id": 456,
        "combo_window_seconds": 90,
    }
    store.observe_fishing_catches(**common, catch_count=0, observed_at="2026-08-09T00:00:00+00:00")

    rewards = store.observe_fishing_catches(
        **common, catch_count=5, observed_at="2026-08-09T00:00:10+00:00"
    )
    replay = store.observe_fishing_catches(
        **common, catch_count=5, observed_at="2026-08-09T00:00:20+00:00"
    )

    assert [(event.combo_count, event.reward_xp) for event in rewards] == [
        (1, 2),
        (2, 5),
        (3, 7),
        (4, 7),
        (5, 10),
    ]
    assert replay == []
    assert store.list_pending_fishing_reward_deliveries() == rewards


def test_fishing_reward_is_excluded_from_regular_xp_observation(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    store.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=100,
        observed_at="2026-08-09T00:00:00+00:00",
    )
    store.observe_fishing_catches(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        catch_count=0,
        observed_at="2026-08-09T00:00:00+00:00",
        combo_window_seconds=90,
    )
    store.observe_fishing_catches(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        catch_count=2,
        observed_at="2026-08-09T00:00:10+00:00",
        combo_window_seconds=90,
    )
    reward = store.list_pending_fishing_reward_deliveries()[0]

    assert store.reserve_fishing_reward_delivery(
        event_id=reward.event_id,
        account_id=account.id,
        reward_xp=reward.reward_xp,
        observed_at=reward.observed_at,
    )
    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=102,
            observed_at="2026-08-09T00:00:20+00:00",
        )
        is None
    )
    pending_audits = store.list_pending_fishing_audits()
    assert len(pending_audits) == 1
    assert pending_audits[0].event_id == reward.event_id
    assert pending_audits[0].reward_delivered is True
    assert pending_audits[0].audit_delivered is False
    store.mark_fishing_audit_delivered(reward.event_id)
    assert store.list_pending_fishing_audits() == []


def test_minecraft_xp_exchange_reward_is_not_observed_as_new_gain(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    store.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=50,
        observed_at="2026-08-05T00:00:00+00:00",
    )
    claim_token = store.get_or_create_minecraft_xp_exchange_claim_token("exchange-7")
    delivery_args = {
        "exchange_id": "exchange-7",
        "level_exchange_id": 7,
        "account_id": account.id,
        "discord_user_id": 123,
        "guild_id": 456,
        "player_name": "Steve",
        "cost_xp": 10,
        "reward_xp": 100,
        "claim_token": claim_token,
        "current_xp": 50,
    }

    assert store.reserve_minecraft_xp_exchange_delivery(
        **delivery_args,
        observed_at="2026-08-05T00:00:01+00:00",
    )
    assert not store.reserve_minecraft_xp_exchange_delivery(
        **delivery_args,
        observed_at="2026-08-05T00:00:02+00:00",
    )
    assert store.has_minecraft_xp_exchange_delivery("exchange-7")
    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=150,
            observed_at="2026-08-05T00:00:30+00:00",
        )
        is None
    )


def test_explicit_exchange_failure_releases_reservation_and_baseline(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    claim_token = store.get_or_create_minecraft_xp_exchange_claim_token("exchange-8")
    assert store.reserve_minecraft_xp_exchange_delivery(
        exchange_id="exchange-8",
        level_exchange_id=8,
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        player_name="Steve",
        cost_xp=10,
        reward_xp=100,
        claim_token=claim_token,
        current_xp=50,
        observed_at="2026-08-05T00:00:01+00:00",
    )

    store.release_minecraft_xp_exchange_delivery(
        exchange_id="exchange-8",
        account_id=account.id,
        current_xp=50,
        observed_at="2026-08-05T00:00:02+00:00",
    )

    assert not store.has_minecraft_xp_exchange_delivery("exchange-8")
    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=50,
            observed_at="2026-08-05T00:00:30+00:00",
        )
        is None
    )


def test_claims_each_advancement_reward_once_and_replays_same_log(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    arguments = {
        "event_id": "advancement-event-1",
        "account_id": account.id,
        "advancement": "Stone Age",
        "discord_user_id": 123,
        "guild_id": 456,
        "minecraft_xp": 10_000,
        "observed_at": "2026-08-04T00:00:00+00:00",
    }

    claimed = store.claim_advancement_reward(**arguments)
    replayed = store.claim_advancement_reward(**arguments)
    duplicate = store.claim_advancement_reward(**(arguments | {"event_id": "advancement-event-2"}))

    assert claimed is not None
    assert claimed.minecraft_xp == 10_000
    assert replayed == claimed
    assert duplicate is None
    assert store.list_minecraft_xp_outbox() == [claimed]
    assert not store.is_advancement_minecraft_reward_delivered(claimed.event_id)

    store.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=50,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    assert store.reserve_advancement_minecraft_reward_delivery(
        event_id=claimed.event_id,
        account_id=account.id,
        reward_xp=100,
        observed_at="2026-08-04T00:00:01+00:00",
    )
    assert not store.reserve_advancement_minecraft_reward_delivery(
        event_id=claimed.event_id,
        account_id=account.id,
        reward_xp=100,
        observed_at="2026-08-04T00:00:02+00:00",
    )

    assert store.is_advancement_minecraft_reward_delivered(claimed.event_id)
    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=150,
            observed_at="2026-08-04T00:00:30+00:00",
            double_in_game_xp=True,
        )
        is None
    )


def test_releases_advancement_minecraft_reward_after_explicit_failure(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    reward = store.claim_advancement_reward(
        event_id="advancement-event-1",
        account_id=account.id,
        advancement="Stone Age",
        discord_user_id=123,
        guild_id=456,
        minecraft_xp=10_000,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    assert reward is not None
    store.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=50,
        observed_at="2026-08-04T00:00:00+00:00",
    )

    assert store.reserve_advancement_minecraft_reward_delivery(
        event_id=reward.event_id,
        account_id=account.id,
        reward_xp=100,
        observed_at=reward.observed_at,
    )
    store.release_advancement_minecraft_reward_delivery(
        event_id=reward.event_id,
        account_id=account.id,
        reward_xp=100,
    )

    assert not store.is_advancement_minecraft_reward_delivered(reward.event_id)
    gained = store.observe_minecraft_xp(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        current_xp=50,
        observed_at="2026-08-04T00:00:30+00:00",
        double_in_game_xp=True,
    )
    assert gained is None


def test_only_one_store_can_reserve_advancement_minecraft_reward(tmp_path) -> None:
    database = tmp_path / "accounts.db"
    store = AccountStore(database)
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    reward = store.claim_advancement_reward(
        event_id="advancement-event-1",
        account_id=account.id,
        advancement="Stone Age",
        discord_user_id=123,
        guild_id=456,
        minecraft_xp=10_000,
        observed_at="2026-08-04T00:00:00+00:00",
    )
    assert reward is not None
    store.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=0,
        observed_at=reward.observed_at,
    )
    barrier = Barrier(2)

    def reserve() -> bool:
        barrier.wait()
        return AccountStore(database).reserve_advancement_minecraft_reward_delivery(
            event_id=reward.event_id,
            account_id=account.id,
            reward_xp=100,
            observed_at=reward.observed_at,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reserve(), range(2)))

    assert sorted(results) == [False, True]
    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=100,
            observed_at="2026-08-04T00:00:30+00:00",
            double_in_game_xp=True,
        )
        is None
    )


def test_event_driven_woodcutting_deduplicates_non_reward_events(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    common = {
        "account_id": account.id,
        "discord_user_id": 123,
        "guild_id": 456,
        "combo_window_seconds": 30,
    }

    for index in range(1, 5):
        assert (
            store.record_woodcutting_log(
                **common,
                event_id=f"00000000-0000-0000-0000-{index:012d}",
                observed_at=f"2026-08-11T00:00:0{index}+00:00",
            )
            is None
        )
    assert (
        store.record_woodcutting_log(
            **common,
            event_id="00000000-0000-0000-0000-000000000001",
            observed_at="2026-08-11T00:00:05+00:00",
        )
        is None
    )
    reward = store.record_woodcutting_log(
        **common,
        event_id="00000000-0000-0000-0000-000000000005",
        observed_at="2026-08-11T00:00:05+00:00",
    )

    assert reward is not None
    assert (reward.log_count, reward.combo_count, reward.reward_xp) == (5, 5, 5)
    assert store.list_pending_woodcutting_reward_deliveries() == [reward]


def test_tracks_woodcutting_milestones_resets_and_excludes_reward_from_sync(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()
    account = store.create_registration(
        edition="java",
        minecraft_name="Steve",
        server_player_name="Steve",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="active",
        created_by=123,
    )
    store.set_minecraft_xp_observation(
        account_id=account.id,
        current_xp=50,
        observed_at="2026-08-04T00:00:00+00:00",
    )

    assert not store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=100,
        observed_at="2026-08-04T00:00:00+00:00",
        combo_window_seconds=30,
    )
    rewards = store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=110,
        observed_at="2026-08-04T00:00:02+00:00",
        combo_window_seconds=30,
    )
    assert [(event.combo_count, event.reward_xp) for event in rewards] == [(5, 5), (10, 15)]
    first = rewards[0]
    assert store.reserve_woodcutting_reward_delivery(
        event_id=first.event_id,
        account_id=account.id,
        reward_xp=first.reward_xp,
        observed_at=first.observed_at,
    )
    assert not store.reserve_woodcutting_reward_delivery(
        event_id=first.event_id,
        account_id=account.id,
        reward_xp=first.reward_xp,
        observed_at=first.observed_at,
    )
    assert (
        store.observe_minecraft_xp(
            account_id=account.id,
            discord_user_id=123,
            guild_id=456,
            current_xp=55,
            observed_at="2026-08-04T00:00:03+00:00",
            double_in_game_xp=True,
        )
        is None
    )

    assert not store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=111,
        observed_at="2026-08-04T00:00:33+00:00",
        combo_window_seconds=30,
    )
    reset_reward = store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=115,
        observed_at="2026-08-04T00:00:35+00:00",
        combo_window_seconds=30,
    )
    assert [(event.combo_count, event.reward_xp) for event in reset_reward] == [(5, 5)]

    assert not store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=116,
        observed_at="2026-08-04T00:01:05+00:00",
        combo_window_seconds=30,
    )
    extended_rewards = store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=130,
        observed_at="2026-08-04T00:01:07+00:00",
        combo_window_seconds=30,
    )
    assert [(event.combo_count, event.reward_xp) for event in extended_rewards] == [
        (10, 15),
        (20, 30),
    ]
    public_reward = extended_rewards[-1]
    assert store.reserve_woodcutting_reward_delivery(
        event_id=public_reward.event_id,
        account_id=account.id,
        reward_xp=public_reward.reward_xp,
        observed_at=public_reward.observed_at,
    )
    pending_public = store.list_pending_woodcutting_public_deliveries()
    assert [event.event_id for event in pending_public] == [public_reward.event_id]
    store.mark_woodcutting_public_delivered(public_reward.event_id, "minecraft")
    partially_delivered = store.list_pending_woodcutting_public_deliveries()
    assert partially_delivered[0].minecraft_public_delivered
    assert not partially_delivered[0].discord_public_delivered
    store.mark_woodcutting_public_delivered(public_reward.event_id, "discord")
    assert store.list_pending_woodcutting_public_deliveries() == []
    assert not store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=131,
        observed_at="2026-08-04T00:01:37+00:00",
        combo_window_seconds=30,
    )
    long_combo_reward = store.observe_woodcutting_logs(
        account_id=account.id,
        discord_user_id=123,
        guild_id=456,
        log_count=140,
        observed_at="2026-08-04T00:01:39+00:00",
        combo_window_seconds=30,
    )
    assert [(event.combo_count, event.reward_xp) for event in long_combo_reward] == [(30, 30)]
