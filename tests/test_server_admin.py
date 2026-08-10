import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

import mc_bot.bot as bot_module
from mc_bot.accounts import WHITELIST_RETRY_LIMIT, AccountStore
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.server_admin import (
    announcement_command,
    clean_rcon_output,
    kick_command,
    parse_online_players,
    read_cached_player_profile,
    read_cached_player_profile_by_uuid,
    read_whitelist_enabled,
    read_whitelisted_players,
    remove_whitelisted_player,
    upsert_whitelisted_player,
    validate_rcon_response,
)
from mc_bot.settings import RuntimeSettings, SettingsStore

STEVE_UUID = "8667ba71-b85a-4004-af54-457a9734eed7"


class FakeRcon:
    def __init__(self, properties_path=None, events=None) -> None:
        self.commands: list[str] = []
        self.properties_path = properties_path
        self.events = events

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if self.events is not None:
            self.events.append(f"rcon:{command}")
        if self.properties_path is not None and command in {"whitelist on", "whitelist off"}:
            enabled = command == "whitelist on"
            self.properties_path.write_text(
                f"white-list={'true' if enabled else 'false'}\n",
                encoding="utf-8",
            )
        return "Whitelist is now turned on" if command == "whitelist on" else "ok"


class RecordingSettingsStore(SettingsStore):
    def __init__(self, path, events) -> None:
        super().__init__(path)
        self.events = events

    def save(self, settings: RuntimeSettings) -> None:
        state = "pause" if settings.whitelist_resume_at is not None else "clear"
        self.events.append(f"save:{state}")
        super().save(settings)


class WhitelistRcon:
    def __init__(self, whitelist_path, *, response="Added Steve to the whitelist") -> None:
        self.whitelist_path = whitelist_path
        self.response = response
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if self.response.startswith("Added"):
            self.whitelist_path.write_text(
                json.dumps([{"uuid": STEVE_UUID, "name": "Steve"}]),
                encoding="utf-8",
            )
        return self.response


class NoopWhitelistRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        return "ok"


class BedrockUuidWhitelistRcon:
    def __init__(self, whitelist_path, player_uuid: str, player_name: str) -> None:
        self.whitelist_path = whitelist_path
        self.player_uuid = player_uuid
        self.player_name = player_name
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
        if command == f"fwhitelist add {self.player_uuid}":
            self.whitelist_path.write_text(
                json.dumps([{"uuid": self.player_uuid, "name": self.player_name}]),
                encoding="utf-8",
            )
        elif command == f"fwhitelist remove {self.player_uuid}":
            self.whitelist_path.write_text("[]", encoding="utf-8")
        return "ok"


class FakeInteractionResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, *, ephemeral=False) -> None:
        self.deferred = ephemeral


class FakeInteractionFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.messages.append(kwargs)


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeInteractionResponse()
        self.followup = FakeInteractionFollowup()


def test_parses_online_players_from_rcon_list() -> None:
    response = "There are 3 of a max of 20 players online: Steve, .Bedrock_User, Alex"

    assert parse_online_players(response) == ["Steve", ".Bedrock_User", "Alex"]


def test_parses_empty_online_player_list() -> None:
    assert parse_online_players("There are 0 of a max of 20 players online:") == []


def test_rejects_unexpected_online_player_name() -> None:
    with pytest.raises(ValueError, match="読み取れません"):
        parse_online_players("There are 1 of a max of 20 players online: bad name")


def test_rejects_failed_or_inconsistent_online_player_response() -> None:
    with pytest.raises(ValueError, match="読み取れません"):
        parse_online_players("Unknown command")
    with pytest.raises(ValueError, match="一致しません"):
        parse_online_players("There are 2 of a max of 20 players online: Steve")


def test_reads_and_sorts_whitelisted_players(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text(
        json.dumps(
            [
                {"uuid": "1", "name": "Steve"},
                {"uuid": "2", "name": ".Bedrock_User"},
                {"uuid": "3", "name": "alex"},
            ]
        ),
        encoding="utf-8",
    )

    assert read_whitelisted_players(whitelist_path) == [".Bedrock_User", "alex", "Steve"]


def test_atomically_upserts_whitelist_without_losing_existing_entries(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text(
        json.dumps([{"uuid": "8667ba71-b85a-4004-af54-457a9734eed7", "name": "Steve"}]),
        encoding="utf-8",
    )

    upsert_whitelisted_player(
        whitelist_path,
        ".Bedrock_User",
        "00000000-0000-0000-0009-123456789abc",
    )

    assert json.loads(whitelist_path.read_text(encoding="utf-8")) == [
        {"uuid": "8667ba71-b85a-4004-af54-457a9734eed7", "name": "Steve"},
        {"uuid": "00000000-0000-0000-0009-123456789abc", "name": ".Bedrock_User"},
    ]


def test_whitelist_upsert_refuses_recycled_name_with_different_uuid(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    original = [{"uuid": "00000000-0000-0000-0009-01fb7be05000", "name": ".SameName"}]
    whitelist_path.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(ValueError, match="別のUUID"):
        upsert_whitelisted_player(
            whitelist_path,
            ".SameName",
            "00000000-0000-0000-0009-01fb7be05001",
        )

    assert json.loads(whitelist_path.read_text(encoding="utf-8")) == original


def test_whitelist_upsert_refuses_ambiguous_duplicate_uuid(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    target_uuid = "00000000-0000-0000-0009-01fb7be05000"
    original = [
        {"uuid": target_uuid, "name": ".OldName"},
        {"uuid": target_uuid, "name": ".CurrentName"},
    ]
    whitelist_path.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(ValueError, match="複数登録"):
        upsert_whitelisted_player(whitelist_path, ".CurrentName", target_uuid)

    assert json.loads(whitelist_path.read_text(encoding="utf-8")) == original


def test_removes_whitelist_entry_by_uuid_when_name_changed(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    target_uuid = "00000000-0000-0000-0009-01fb7be05000"
    whitelist_path.write_text(
        json.dumps(
            [
                {"uuid": "8667ba71-b85a-4004-af54-457a9734eed7", "name": "Steve"},
                {"uuid": target_uuid, "name": ".BuckedAtol84031"},
            ]
        ),
        encoding="utf-8",
    )

    assert remove_whitelisted_player(whitelist_path, target_uuid) is True

    assert json.loads(whitelist_path.read_text(encoding="utf-8")) == [
        {"uuid": "8667ba71-b85a-4004-af54-457a9734eed7", "name": "Steve"}
    ]


def test_reads_bedrock_profile_from_minecraft_usercache(tmp_path) -> None:
    usercache_path = tmp_path / "usercache.json"
    usercache_path.write_text(
        json.dumps(
            [
                {
                    "name": ".Bedrock_User",
                    "uuid": "00000000-0000-0000-0009-123456789abc",
                    "expiresOn": "2026-09-01 00:00:00 +0900",
                }
            ]
        ),
        encoding="utf-8",
    )

    assert read_cached_player_profile(usercache_path, ".bedrock_user") == (
        ".Bedrock_User",
        "00000000-0000-0000-0009-123456789abc",
    )
    assert read_cached_player_profile(usercache_path, ".missing") is None


def test_refuses_ambiguous_player_name_in_minecraft_usercache(tmp_path) -> None:
    usercache_path = tmp_path / "usercache.json"
    usercache_path.write_text(
        json.dumps(
            [
                {
                    "name": ".RecycledName",
                    "uuid": "00000000-0000-0000-0009-01fb7be05000",
                },
                {
                    "name": ".recycledname",
                    "uuid": "00000000-0000-0000-0009-01fb7be05001",
                },
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="複数のUUID"):
        read_cached_player_profile(usercache_path, ".RecycledName")


def test_reads_current_player_name_from_minecraft_usercache_by_uuid(tmp_path) -> None:
    usercache_path = tmp_path / "usercache.json"
    usercache_path.write_text(
        json.dumps(
            [
                {
                    "name": ".CurrentName",
                    "uuid": "00000000-0000-0000-0009-01fb7be05000",
                }
            ]
        ),
        encoding="utf-8",
    )

    assert read_cached_player_profile_by_uuid(
        usercache_path, "00000000-0000-0000-0009-01fb7be05000"
    ) == (".CurrentName", "00000000-0000-0000-0009-01fb7be05000")


def test_rejects_invalid_whitelist_file(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text('{"name": "Steve"}', encoding="utf-8")

    with pytest.raises(ValueError, match="読み取れません"):
        read_whitelisted_players(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="形式"):
        read_whitelisted_players(whitelist_path)


def test_validates_rcon_command_response() -> None:
    assert validate_rcon_response("§aSet the time to 1000") == "Set the time to 1000"
    with pytest.raises(ValueError, match="Unknown command"):
        validate_rcon_response("Unknown command. Type /help for help.")
    with pytest.raises(ValueError, match="No entity"):
        validate_rcon_response("No entity was found")


def test_reads_actual_whitelist_state(tmp_path) -> None:
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("motd=Test\nwhite-list=true\n", encoding="utf-8")

    assert read_whitelist_enabled(properties_path) is True

    properties_path.write_text("white-list=false\n", encoding="utf-8")
    assert read_whitelist_enabled(properties_path) is False


def test_rejects_missing_whitelist_property(tmp_path) -> None:
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("motd=Test\n", encoding="utf-8")

    with pytest.raises(ValueError, match="white-list"):
        read_whitelist_enabled(properties_path)


def test_builds_kick_command_with_normalized_reason() -> None:
    assert kick_command(".Bedrock_User", "荒らし\n行為") == "kick .Bedrock_User 荒らし 行為"


def test_rejects_invalid_kick_player_name() -> None:
    with pytest.raises(ValueError, match="無効"):
        kick_command("@a", "reason")


def test_builds_safe_tellraw_announcement() -> None:
    command = announcement_command('"テスト" @everyone')
    prefix = "tellraw @a "

    assert command.startswith(prefix)
    assert json.loads(command.removeprefix(prefix)) == {
        "text": '[サーバー告知] "テスト" @everyone',
        "color": "gold",
    }


def test_cleans_minecraft_formatting_and_limits_output() -> None:
    assert clean_rcon_output("§aHealthy\r\n", limit=6) == "Healt…"


def test_automatically_resumes_persisted_whitelist_pause(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("white-list=false\n", encoding="utf-8")
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            settings_path=settings_path,
            rcon_password="secret",
            minecraft_server_properties_path=properties_path,
        )
    )
    bot._settings = RuntimeSettings(whitelist_resume_at=1)
    rcon = FakeRcon(properties_path)
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._resume_whitelist_if_due())

    assert rcon.commands == ["whitelist on"]
    assert read_whitelist_enabled(properties_path) is True
    assert bot._settings.whitelist_resume_at is None
    assert SettingsStore(settings_path).load().whitelist_resume_at is None


def test_persists_resume_deadline_before_disabling_whitelist(tmp_path) -> None:
    events: list[str] = []
    settings_path = tmp_path / "settings.json"
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("white-list=true\n", encoding="utf-8")
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            settings_path=settings_path,
            rcon_password="secret",
            minecraft_server_properties_path=properties_path,
        )
    )
    bot._settings_store = RecordingSettingsStore(settings_path, events)
    bot._rcon = FakeRcon(properties_path, events)  # type: ignore[assignment]

    asyncio.run(bot._pause_whitelist_for(15))

    assert events == ["save:pause", "rcon:whitelist off"]
    assert read_whitelist_enabled(properties_path) is False
    assert bot._settings.whitelist_resume_at is not None


def test_serializes_whitelist_pause_and_resume(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    bot = MinecraftDiscordBot(Config(discord_token="secret", settings_path=settings_path))
    active_operations = 0
    maximum_active_operations = 0

    async def fake_set_whitelist_enabled(enabled: bool) -> None:
        nonlocal active_operations, maximum_active_operations
        active_operations += 1
        maximum_active_operations = max(maximum_active_operations, active_operations)
        await asyncio.sleep(0)
        active_operations -= 1

    bot._set_whitelist_enabled = fake_set_whitelist_enabled  # type: ignore[method-assign]

    async def exercise() -> None:
        pause = asyncio.create_task(bot._pause_whitelist_for(15))
        await asyncio.sleep(0)
        resume = asyncio.create_task(bot._resume_whitelist_now())
        await asyncio.gather(pause, resume)

    asyncio.run(exercise())

    assert maximum_active_operations == 1
    assert bot._settings.whitelist_resume_at is None


def test_marks_registration_active_only_after_whitelist_file_is_updated(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text("[]", encoding="utf-8")
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
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
        player_uuid=STEVE_UUID,
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
    )
    bot._resolve_java_profile_by_uuid = AsyncMock(  # type: ignore[method-assign]
        return_value=("Steve", STEVE_UUID)
    )
    rcon = WhitelistRcon(whitelist_path)
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._add_to_whitelist(account))

    assert rcon.commands == ["whitelist add Steve"]
    assert store.get(account.id).status == "active"  # type: ignore[union-attr]
    assert read_whitelisted_players(whitelist_path) == ["Steve"]


def test_keeps_registration_pending_when_rcon_command_fails(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text("[]", encoding="utf-8")
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
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
        player_uuid=STEVE_UUID,
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
    )
    bot._resolve_java_profile_by_uuid = AsyncMock(  # type: ignore[method-assign]
        return_value=("Steve", STEVE_UUID)
    )
    bot._rcon = WhitelistRcon(  # type: ignore[assignment]
        whitelist_path,
        response="Unknown command. Type /help for help.",
    )

    with pytest.raises(ValueError, match="Unknown command"):
        asyncio.run(bot._add_to_whitelist(account))

    assert store.get(account.id).status == "pending_add"  # type: ignore[union-attr]
    assert read_whitelisted_players(whitelist_path) == []


def test_relink_readds_removed_whitelist_and_skips_stale_removal_retry(tmp_path) -> None:
    async def exercise() -> None:
        whitelist_path = tmp_path / "whitelist.json"
        whitelist_path.write_text("[]", encoding="utf-8")
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
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
            player_uuid=STEVE_UUID,
        )
        store.update_status(account.id, "pending_remove")
        stale_removal = store.get(account.id)
        assert stale_removal is not None
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                accounts_path=accounts_path,
                minecraft_whitelist_path=whitelist_path,
                rcon_password="secret",
            )
        )
        bot._resolve_java_profile_by_uuid = AsyncMock(  # type: ignore[method-assign]
            return_value=("Steve", STEVE_UUID)
        )
        rcon = WhitelistRcon(whitelist_path)
        bot._rcon = rcon  # type: ignore[assignment]
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        bot._audit_server_action = Mock()  # type: ignore[method-assign]
        target = Mock()
        target.id = 456
        target.display_name = "correct-user"
        target.mention = "<@456>"
        interaction = Mock()
        interaction.user.id = 999
        interaction.guild_id = 1001
        interaction.response.edit_message = AsyncMock()

        await bot.reassign_account_link(
            interaction,
            account_id=account.id,
            expected_discord_user_id=123,
            target=target,
            recover_pending_remove=True,
        )
        await bot._remove_from_whitelist(stale_removal)

        changed = store.get(account.id)
        assert changed is not None
        assert changed.discord_user_id == 456
        assert changed.status == "active"
        assert read_whitelisted_players(whitelist_path) == ["Steve"]
        assert rcon.commands == ["whitelist add Steve"]

    asyncio.run(exercise())


def test_falls_back_to_direct_whitelist_update_when_rcon_is_not_reflected(
    tmp_path, monkeypatch
) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text("[]", encoding="utf-8")
    (tmp_path / "usercache.json").write_text(
        json.dumps([{"uuid": "8667ba71-b85a-4004-af54-457a9734eed7", "name": "Steve"}]),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
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
        player_uuid=STEVE_UUID,
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
    )
    bot._resolve_java_profile_by_uuid = AsyncMock(  # type: ignore[method-assign]
        return_value=("Steve", STEVE_UUID)
    )
    rcon = NoopWhitelistRcon()
    bot._rcon = rcon  # type: ignore[assignment]

    async def exercise() -> None:
        monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())
        await bot._add_to_whitelist(account)

    asyncio.run(exercise())

    assert rcon.commands == ["whitelist add Steve", "whitelist reload"]
    assert read_whitelisted_players(whitelist_path) == ["Steve"]
    assert store.get(account.id).status == "active"  # type: ignore[union-attr]


def test_bedrock_whitelist_add_and_remove_use_stable_uuid(tmp_path) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text("[]", encoding="utf-8")
    (tmp_path / "usercache.json").write_text(
        json.dumps([{"uuid": player_uuid, "name": ".yuki19911261"}]),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
    store.initialize()
    account = store.create_registration(
        edition="bedrock",
        minecraft_name="yuki19911261",
        server_player_name=".yuki19911261",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="user",
        source="self",
        status="pending_add",
        created_by=123,
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
    )
    rcon = BedrockUuidWhitelistRcon(whitelist_path, player_uuid, ".yuki19911261")
    bot._rcon = rcon  # type: ignore[assignment]

    async def exercise() -> None:
        await bot._add_to_whitelist(account)
        active = store.get(account.id)
        assert active is not None
        await bot._remove_from_whitelist(active)

    asyncio.run(exercise())

    assert rcon.commands == [
        f"fwhitelist add {player_uuid}",
        f"fwhitelist remove {player_uuid}",
        'kick ".yuki19911261" Discordの参加登録が解除されました',
    ]
    assert store.get(account.id).status == "missing"  # type: ignore[union-attr]


def test_bedrock_removal_falls_back_to_direct_uuid_update(tmp_path, monkeypatch) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text(
        json.dumps([{"uuid": player_uuid, "name": ".BuckedAtol84031"}]),
        encoding="utf-8",
    )
    (tmp_path / "usercache.json").write_text(
        json.dumps([{"uuid": player_uuid, "name": ".yuki19911261"}]),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
    store.initialize()
    account = store.create_registration(
        edition="bedrock",
        minecraft_name="yuki19911261",
        server_player_name=".yuki19911261",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="user",
        source="self",
        status="active",
        created_by=123,
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
    )
    rcon = NoopWhitelistRcon()
    bot._rcon = rcon  # type: ignore[assignment]
    monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())

    asyncio.run(bot._remove_from_whitelist(account))

    assert rcon.commands == [
        f"fwhitelist remove {player_uuid}",
        "whitelist reload",
        'kick ".yuki19911261" Discordの参加登録が解除されました',
    ]
    assert read_whitelisted_players(whitelist_path) == []
    assert store.get(account.id).status == "missing"  # type: ignore[union-attr]


def test_bedrock_removal_skips_kick_when_current_name_is_not_uuid_verified(tmp_path) -> None:
    player_uuid = "00000000-0000-0000-0009-01fb7be05000"
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text(
        json.dumps([{"uuid": player_uuid, "name": ".CurrentName"}]),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
    store.initialize()
    account = store.create_registration(
        edition="bedrock",
        minecraft_name="OldName",
        server_player_name=".OldName",
        player_uuid=player_uuid,
        discord_user_id=123,
        discord_username="user",
        source="self",
        status="active",
        created_by=123,
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
    )
    rcon = BedrockUuidWhitelistRcon(whitelist_path, player_uuid, ".CurrentName")
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._remove_from_whitelist(account))

    assert rcon.commands == [f"fwhitelist remove {player_uuid}"]
    assert store.get(account.id).status == "missing"  # type: ignore[union-attr]


def test_runtime_player_name_is_refreshed_by_uuid_from_usercache(tmp_path) -> None:
    async def exercise() -> None:
        player_uuid = "00000000-0000-0000-0009-01fb7be05000"
        whitelist_path = tmp_path / "whitelist.json"
        whitelist_path.write_text(
            json.dumps([{"uuid": player_uuid, "name": ".BuckedAtol84031"}]),
            encoding="utf-8",
        )
        (tmp_path / "usercache.json").write_text(
            json.dumps([{"uuid": player_uuid, "name": ".yuki19911261"}]),
            encoding="utf-8",
        )
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
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
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                accounts_path=accounts_path,
                minecraft_whitelist_path=whitelist_path,
            )
        )

        matched = await bot._find_account_for_player_name(".yuki19911261")

        assert matched is not None
        assert matched.id == account.id
        assert matched.server_player_name == ".yuki19911261"
        assert matched.minecraft_name == "yuki19911261"
        assert matched.player_uuid == player_uuid
        assert len(store.list_whitelist_registrations()) == 1

    asyncio.run(exercise())


def test_runtime_uuid_account_is_not_matched_by_name_without_usercache(tmp_path) -> None:
    async def exercise() -> None:
        whitelist_path = tmp_path / "whitelist.json"
        whitelist_path.write_text("[]", encoding="utf-8")
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        store.create_registration(
            edition="bedrock",
            minecraft_name="RecycledName",
            server_player_name=".RecycledName",
            player_uuid="00000000-0000-0000-0009-01fb7be05000",
            discord_user_id=123,
            discord_username="old-owner",
            source="self",
            status="active",
            created_by=123,
        )
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                accounts_path=accounts_path,
                minecraft_whitelist_path=whitelist_path,
            )
        )

        assert await bot._find_account_for_player_name(".RecycledName") is None

    asyncio.run(exercise())


def test_runtime_legacy_account_still_uses_name_when_usercache_is_unavailable(tmp_path) -> None:
    async def exercise() -> None:
        whitelist_path = tmp_path / "whitelist.json"
        whitelist_path.write_text("[]", encoding="utf-8")
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        account = store.create_registration(
            edition="java",
            minecraft_name="LegacyName",
            server_player_name="LegacyName",
            discord_user_id=123,
            discord_username="legacy-owner",
            source="self",
            status="active",
            created_by=123,
        )
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                accounts_path=accounts_path,
                minecraft_whitelist_path=whitelist_path,
            )
        )

        matched = await bot._find_account_for_player_name("LegacyName")

        assert matched is not None
        assert matched.id == account.id
        assert matched.player_uuid is None

    asyncio.run(exercise())


def test_resolves_bedrock_uuid_through_public_profile_fallback(tmp_path, monkeypatch) -> None:
    class JsonResponse:
        def __init__(self, status, payload) -> None:
            self.status = status
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def json(self, *, content_type=None):
            return self.payload

    class JsonSession:
        def __init__(self, *, timeout) -> None:
            self.responses = [
                JsonResponse(404, {}),
                JsonResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "player": {
                                "id": "281474976710655",
                                "username": "Bedrock User",
                            }
                        },
                    },
                ),
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        def get(self, _url):
            return self.responses.pop(0)

    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
    store.initialize()
    account = store.create_registration(
        edition="bedrock",
        minecraft_name="Bedrock User",
        server_player_name=".Bedrock_User",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="pending_add",
        created_by=123,
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=tmp_path / "whitelist.json",
        )
    )
    monkeypatch.setattr(bot_module.aiohttp, "ClientSession", JsonSession)

    player_name, player_uuid = asyncio.run(bot._resolve_whitelist_profile(account))

    assert player_name == ".Bedrock_User"
    assert player_uuid == "00000000-0000-0000-0000-ffffffffffff"
    assert store.get(account.id).player_uuid == player_uuid  # type: ignore[union-attr]


def test_resolves_current_java_name_from_stored_uuid(tmp_path, monkeypatch) -> None:
    class JsonResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def json(self, *, content_type=None):
            return {"id": STEVE_UUID.replace("-", ""), "name": "CurrentSteve"}

    class JsonSession:
        def __init__(self, *, timeout) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        def get(self, url):
            assert url.endswith(STEVE_UUID.replace("-", ""))
            return JsonResponse()

    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text("[]", encoding="utf-8")
    bot = MinecraftDiscordBot(
        Config(discord_token="secret", minecraft_whitelist_path=whitelist_path)
    )
    monkeypatch.setattr(bot_module.aiohttp, "ClientSession", JsonSession)

    profile = asyncio.run(
        bot._resolve_player_profile(
            edition="java",
            minecraft_name="OldSteve",
            server_player_name="OldSteve",
            stored_uuid=STEVE_UUID,
        )
    )

    assert profile == ("CurrentSteve", STEVE_UUID)


def test_rejects_java_session_profile_with_different_uuid(tmp_path, monkeypatch) -> None:
    class JsonResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def json(self, *, content_type=None):
            return {
                "id": "00000000000000000000000000000001",
                "name": "CurrentSteve",
            }

    class JsonSession:
        def __init__(self, *, timeout) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        def get(self, _url):
            return JsonResponse()

    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text("[]", encoding="utf-8")
    bot = MinecraftDiscordBot(
        Config(discord_token="secret", minecraft_whitelist_path=whitelist_path)
    )
    monkeypatch.setattr(bot_module.aiohttp, "ClientSession", JsonSession)

    with pytest.raises(RuntimeError, match="一致しません"):
        asyncio.run(bot._resolve_java_profile_by_uuid(STEVE_UUID))


def test_java_whitelist_commands_use_uuid_verified_current_name(tmp_path) -> None:
    class RenamedJavaWhitelistRcon:
        def __init__(self, whitelist_path) -> None:
            self.whitelist_path = whitelist_path
            self.commands: list[str] = []

        def execute(self, command: str) -> str:
            self.commands.append(command)
            if command == "whitelist add CurrentSteve":
                self.whitelist_path.write_text(
                    json.dumps([{"uuid": STEVE_UUID, "name": "CurrentSteve"}]),
                    encoding="utf-8",
                )
            elif command == "whitelist remove CurrentSteve":
                self.whitelist_path.write_text("[]", encoding="utf-8")
            return "ok"

    async def exercise() -> None:
        whitelist_path = tmp_path / "whitelist.json"
        whitelist_path.write_text("[]", encoding="utf-8")
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        account = store.create_registration(
            edition="java",
            minecraft_name="OldSteve",
            server_player_name="OldSteve",
            player_uuid=STEVE_UUID,
            discord_user_id=123,
            discord_username="user",
            source="self",
            status="pending_add",
            created_by=123,
        )
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                accounts_path=accounts_path,
                minecraft_whitelist_path=whitelist_path,
                rcon_password="secret",
            )
        )
        bot._resolve_java_profile_by_uuid = AsyncMock(  # type: ignore[method-assign]
            return_value=("CurrentSteve", STEVE_UUID)
        )
        rcon = RenamedJavaWhitelistRcon(whitelist_path)
        bot._rcon = rcon  # type: ignore[assignment]

        await bot._add_to_whitelist(account)
        active = store.get(account.id)
        assert active is not None
        assert active.server_player_name == "CurrentSteve"
        await bot._remove_from_whitelist(active)

        assert rcon.commands == [
            "whitelist add CurrentSteve",
            "whitelist remove CurrentSteve",
            'kick "CurrentSteve" Discordの参加登録が解除されました',
        ]
        assert store.get(account.id).status == "missing"  # type: ignore[union-attr]

    asyncio.run(exercise())


def test_sync_repairs_managed_registration_missing_from_whitelist(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text("[]", encoding="utf-8")
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
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
        player_uuid=STEVE_UUID,
    )
    store.update_status(account.id, "active")
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
    )
    bot._resolve_java_profile_by_uuid = AsyncMock(  # type: ignore[method-assign]
        return_value=("Steve", STEVE_UUID)
    )
    rcon = WhitelistRcon(whitelist_path)
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._sync_whitelist_accounts())

    assert rcon.commands == ["whitelist add Steve"]
    assert store.get(account.id).status == "active"  # type: ignore[union-attr]
    assert read_whitelisted_players(whitelist_path) == ["Steve"]


def test_sync_stops_before_reconciliation_when_uuid_owners_conflict(tmp_path) -> None:
    async def exercise() -> None:
        player_uuid = "00000000-0000-0000-0009-01fb7be05000"
        whitelist_path = tmp_path / "whitelist.json"
        whitelist_path.write_text(
            json.dumps([{"uuid": player_uuid, "name": ".SecondName"}]),
            encoding="utf-8",
        )
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
        store.initialize()
        store.create_registration(
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
        with sqlite3.connect(accounts_path) as connection:
            connection.execute(
                "UPDATE minecraft_accounts SET player_uuid = ? WHERE id = ?",
                (player_uuid, second.id),
            )
        bot = MinecraftDiscordBot(
            Config(
                discord_token="secret",
                accounts_path=accounts_path,
                minecraft_whitelist_path=whitelist_path,
            )
        )
        bot._reconcile_pending_actions = AsyncMock()  # type: ignore[method-assign]

        await bot._sync_whitelist_accounts()

        bot._reconcile_pending_actions.assert_not_awaited()

    asyncio.run(exercise())


def test_pending_whitelist_reconciliation_stops_after_retry_limit(tmp_path) -> None:
    async def exercise() -> None:
        accounts_path = tmp_path / "accounts.db"
        store = AccountStore(accounts_path)
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
        bot = MinecraftDiscordBot(Config(discord_token="secret", accounts_path=accounts_path))
        bot._add_to_whitelist = AsyncMock(  # type: ignore[method-assign]
            side_effect=ValueError("Minecraft IDが存在しません")
        )

        for _ in range(WHITELIST_RETRY_LIMIT + 2):
            await bot._reconcile_pending_actions()

        assert bot._add_to_whitelist.await_count == WHITELIST_RETRY_LIMIT
        failed = store.get(account.id)
        assert failed is not None
        assert failed.status == "pending_add"
        assert failed.whitelist_retry_count == WHITELIST_RETRY_LIMIT
        assert failed.whitelist_last_error == "Minecraft IDが存在しません"

    asyncio.run(exercise())


def test_whitelist_overview_includes_unreflected_registrations(tmp_path) -> None:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text(
        json.dumps([{"uuid": "uuid-1", "name": "Steve"}]),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.db"
    store = AccountStore(accounts_path)
    store.initialize()
    account = store.create_registration(
        edition="bedrock",
        minecraft_name="MissingUser",
        server_player_name=".MissingUser",
        discord_user_id=123,
        discord_username="hoge",
        source="self",
        status="pending_add",
        created_by=123,
    )
    store.update_status(account.id, "pending_add")
    for attempt in range(WHITELIST_RETRY_LIMIT):
        store.record_whitelist_retry_failure(
            account.id,
            expected_status="pending_add",
            error=f"failure {attempt + 1}",
        )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
        )
    )
    interaction = FakeInteraction()

    asyncio.run(bot.show_whitelist_entries(interaction))  # type: ignore[arg-type]

    assert interaction.response.deferred
    message = interaction.followup.messages[0]
    assert message["ephemeral"] is True
    assert message["allowed_mentions"].everyone is False
    embed = message["embed"]
    assert embed.title == "🛡️ Whitelist一覧 (実登録1件 / 登録情報1件)"
    assert "**Steve** (未連携)" in embed.description
    assert (
        "**.MissingUser (<@123>)**  ⚠️ Whitelist追加失敗"
        "\uff08自動再試行停止\uff09" in embed.description
    )
