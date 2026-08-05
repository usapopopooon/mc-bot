import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from mc_bot.accounts import AccountStore


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
