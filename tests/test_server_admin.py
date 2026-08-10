import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

import mc_bot.bot as bot_module
from mc_bot.accounts import AccountStore
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.server_admin import (
    announcement_command,
    clean_rcon_output,
    kick_command,
    parse_online_players,
    read_cached_player_profile,
    read_whitelist_enabled,
    read_whitelisted_players,
    upsert_whitelisted_player,
    validate_rcon_response,
)
from mc_bot.settings import RuntimeSettings, SettingsStore


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
                json.dumps([{"uuid": "uuid-1", "name": "Steve"}]),
                encoding="utf-8",
            )
        return self.response


class NoopWhitelistRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str) -> str:
        self.commands.append(command)
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
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
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
    )
    bot = MinecraftDiscordBot(
        Config(
            discord_token="secret",
            accounts_path=accounts_path,
            minecraft_whitelist_path=whitelist_path,
            rcon_password="secret",
        )
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

    async def exercise() -> None:
        monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())
        await bot._add_to_whitelist(account)

    asyncio.run(exercise())

    assert rcon.commands == ["whitelist add Steve", "whitelist reload"]
    assert read_whitelisted_players(whitelist_path) == ["Steve"]
    assert store.get(account.id).status == "active"  # type: ignore[union-attr]


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
    rcon = WhitelistRcon(whitelist_path)
    bot._rcon = rcon  # type: ignore[assignment]

    asyncio.run(bot._sync_whitelist_accounts())

    assert rcon.commands == ["whitelist add Steve"]
    assert store.get(account.id).status == "active"  # type: ignore[union-attr]
    assert read_whitelisted_players(whitelist_path) == ["Steve"]


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
    store.update_status(account.id, "active")
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
    assert "**.MissingUser (<@123>)**  ⚠️ Whitelist未反映" in embed.description
