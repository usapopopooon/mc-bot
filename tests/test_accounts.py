import json

from mc_bot.accounts import AccountStore


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
