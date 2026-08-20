import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.quest import Quest
from mc_bot.quest_ui import (
    QuestActionConfirmationView,
    QuestListingView,
    QuestMineView,
    QuestPanelView,
    quest_action_confirmation_embed,
)
from mc_bot.settings import RuntimeSettings


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.deleted = False
        self.edits: list[dict[str, object]] = []

    async def delete(self) -> None:
        self.deleted = True

    async def edit(self, **options: object) -> None:
        self.edits.append(options)


class FakeChannel:
    def __init__(self, message: FakeMessage | None = None) -> None:
        self.message = message
        self.sent: list[dict[str, object]] = []
        self.next_message_id = 900

    async def fetch_message(self, message_id: int) -> FakeMessage:
        assert self.message is not None
        assert message_id == self.message.id
        return self.message

    async def send(self, **options: object) -> FakeMessage:
        self.sent.append(options)
        self.next_message_id += 1
        self.message = FakeMessage(self.next_message_id)
        return self.message


class NonceChannel:
    def __init__(self) -> None:
        self.messages: list[SimpleNamespace] = []
        self.posts = 0

    async def history(self, **kwargs):  # type: ignore[no-untyped-def]
        for message in reversed(self.messages):
            yield message

    async def send(self, **kwargs):  # type: ignore[no-untyped-def]
        self.posts += 1
        self.messages.append(
            SimpleNamespace(
                author=SimpleNamespace(id=123),
                nonce=kwargs["nonce"],
                embeds=[kwargs["embed"]],
            )
        )
        return FakeMessage(900 + self.posts)


def _quest(*, status: str, message_id: int | None) -> Quest:
    has_worker = status in {"accepted", "completed"}
    return Quest(
        quest_id=17,
        event_id="11111111-1111-4111-8111-111111111111",
        last_transition_id="44444444-4444-4444-8444-444444444444",
        last_transition_kind=status if status in {"accepted", "completed"} else "created",
        owner_account_id=2,
        owner_discord_user_id=2002,
        owner_uuid="22222222-2222-4222-8222-222222222222",
        owner_name="Owner",
        worker_account_id=3 if has_worker else None,
        worker_discord_user_id=2003 if has_worker else None,
        worker_uuid=("33333333-3333-4333-8333-333333333333" if has_worker else None),
        worker_name="Worker" if has_worker else None,
        requested_item_id="minecraft:ancient_debris",
        requested_item_name="古代の残骸",
        requested_count=8,
        reward_item_id="minecraft:diamond",
        reward_item_name="ダイヤモンド",
        reward_count=3,
        fulfillment_hours=24,
        status=status,
        open_expires_at="2026-08-27T00:00:00+00:00",
        accepted_deadline=("2026-08-21T00:00:00+00:00" if has_worker else None),
        discord_message_id=message_id,
        discord_log_delivery_attempted=False,
        discord_log_notified=False,
        created_at="2026-08-20T00:00:00+00:00",
        published_at="2026-08-20T00:01:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
    )


def test_quest_panel_has_guide_mine_and_claim_buttons() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))

    panel = QuestPanelView(bot)
    listing = QuestListingView(bot, 17)

    assert {child.custom_id for child in panel.children} == {
        "mc-quest:guide:0",
        "mc-quest:mine:0",
        "mc-quest:claim:0",
    }
    assert {child.custom_id for child in listing.children} == {"mc-quest:accept:17"}


def test_quest_actions_require_an_explicit_confirmation() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    quest = _quest(status="open", message_id=901)

    view = QuestActionConfirmationView(bot, quest, owner_id=2003, action="accept")
    embed = quest_action_confirmation_embed(quest, "accept")

    assert {child.custom_id for child in view.children} == {
        "mc-quest:confirm-accept:17",
        "mc-quest:confirmation-cancel:17",
    }
    assert "古代の残骸 x8" in (embed.description or "")
    assert "ダイヤモンド x3" in (embed.description or "")
    assert "24時間" in (embed.description or "")


def test_public_accept_button_opens_confirmation_before_running_action() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot.show_quest_action_confirmation = AsyncMock()  # type: ignore[method-assign]
        bot.accept_quest = AsyncMock()  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        button = QuestListingView(bot, 17).children[0]

        await button.callback(interaction)

        bot.show_quest_action_confirmation.assert_awaited_once_with(  # type: ignore[attr-defined]
            interaction, 17, "accept"
        )
        bot.accept_quest.assert_not_awaited()  # type: ignore[attr-defined]

    asyncio.run(exercise())


def test_my_quests_uses_one_item_per_page_without_silent_truncation() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        quests = [
            replace(_quest(status="open", message_id=None), quest_id=value)
            for value in range(30, 18, -1)
        ]
        bot._quests.list_active_for_discord_user = Mock(  # type: ignore[method-assign]
            return_value=quests
        )
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 2002
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        await bot.show_my_quests(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        response = interaction.edit_original_response.await_args.kwargs
        assert response["embed"].title == "依頼中 #30"
        assert response["embed"].footer.text == "1 / 12"
        assert isinstance(response["view"], QuestMineView)
        assert {child.custom_id for child in response["view"].children} >= {
            "mc-quest:cancel:30",
            "mc-quest:mine-page:1",
        }

    asyncio.run(exercise())


def test_refresh_quest_panel_creates_and_persists_message(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(discord_token="secret", settings_path=tmp_path / "settings.json")
    )
    bot._settings = RuntimeSettings(guild_id=1, quest_channel_id=2)
    channel = FakeChannel()
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )

    asyncio.run(bot._refresh_quest_panel())

    assert bot._settings.quest_panel_message_id == 901
    assert len(channel.sent) == 1
    assert isinstance(channel.sent[0]["view"], QuestPanelView)


def test_refresh_quest_panel_reuses_existing_message_on_restart(tmp_path) -> None:
    bot = MinecraftDiscordBot(
        Config(discord_token="secret", settings_path=tmp_path / "settings.json")
    )
    existing_message = FakeMessage(800)
    channel = FakeChannel(existing_message)
    bot._settings = RuntimeSettings(
        guild_id=1,
        quest_channel_id=2,
        quest_panel_message_id=existing_message.id,
    )
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=channel
    )

    asyncio.run(bot._refresh_quest_panel())

    assert not existing_message.deleted
    assert len(existing_message.edits) == 1
    assert channel.sent == []
    assert bot._settings.quest_panel_message_id == 800


def test_accepted_quest_card_is_deleted_instead_of_left_on_board() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(guild_id=1, quest_channel_id=2)
    quest = _quest(status="accepted", message_id=901)
    bot._quests.get = Mock(return_value=quest)  # type: ignore[method-assign]
    bot._quests.set_discord_message = Mock()  # type: ignore[method-assign]
    message = FakeMessage(901)
    bot._resolve_and_validate_channel = AsyncMock(  # type: ignore[method-assign]
        return_value=FakeChannel(message)
    )

    asyncio.run(bot._refresh_quest_listing(17))

    assert message.deleted
    bot._quests.set_discord_message.assert_called_once_with(17, None)  # type: ignore[attr-defined]


def test_quest_log_retry_uses_nonce_to_avoid_duplicate_discord_post() -> None:
    bot = MinecraftDiscordBot(Config(discord_token="secret"))
    bot._settings = RuntimeSettings(guild_id=1, quest_log_channel_id=2)
    quest = _quest(status="completed", message_id=None)
    bot._quests.list_terminal_unnotified = Mock(  # type: ignore[method-assign]
        side_effect=[[quest], [replace(quest, discord_log_delivery_attempted=True)]]
    )
    bot._quests.mark_discord_log_delivery_attempted = Mock()  # type: ignore[method-assign]
    bot._quests.mark_discord_log_notified = Mock(  # type: ignore[method-assign]
        side_effect=[RuntimeError("database stopped after Discord accepted the message"), None]
    )
    channel = NonceChannel()
    bot._resolve_and_validate_channel = AsyncMock(return_value=channel)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="database stopped"):
        asyncio.run(bot._deliver_quest_logs())
    asyncio.run(bot._deliver_quest_logs())

    assert channel.posts == 1
    assert len(channel.messages) == 1
