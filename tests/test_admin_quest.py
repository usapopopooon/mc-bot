import asyncio
from unittest.mock import AsyncMock, Mock

import discord

from mc_bot.admin_quest import (
    AdminQuestCreateModal,
    AdminQuestDraft,
    AdminQuestRetryView,
    AdminQuestSuggestionView,
)
from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config
from mc_bot.translations import MinecraftItemTranslator


def test_item_search_supports_japanese_english_and_ids() -> None:
    translator = MinecraftItemTranslator.load()

    japanese = translator.search("石")
    english = translator.search("Bottle o' Enchanting")
    full_id = translator.search("minecraft:netherite_sword")
    derived_english = translator.search("Poplar Boat")

    assert japanese[0].item_id == "minecraft:stone"
    assert japanese[0].name == "石"
    assert translator.is_exact_match("石", japanese[0])
    assert english[0].item_id == "minecraft:experience_bottle"
    assert english[0].english_name == "Bottle o' Enchanting"
    assert translator.is_exact_match("bottle o' enchanting", english[0])
    assert full_id[0].item_id == "minecraft:netherite_sword"
    assert translator.is_exact_match("minecraft:netherite_sword", full_id[0])
    assert derived_english[0].item_id == "minecraft:poplar_boat"
    assert derived_english[0].english_name == "Poplar Boat"


def test_admin_quest_menu_resolves_exact_japanese_names_before_confirmation() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 9001
        interaction.response.send_message = AsyncMock()
        draft = AdminQuestDraft(
            requested_query="石",
            requested_count=32,
            reward_query="ダイヤモンド",
            reward_count=3,
            fulfillment_hours=24,
        )

        await bot.show_admin_quest_suggestions(interaction, draft)

        response = interaction.response.send_message.await_args.kwargs
        assert response["ephemeral"] is True
        view = response["view"]
        assert isinstance(view, AdminQuestSuggestionView)
        assert view.requested_item_id == "minecraft:stone"
        assert view.reward_item_id == "minecraft:diamond"
        assert view.confirm_button.disabled is False
        assert view.requested_select.options[0].description == "Stone / minecraft:stone"
        content = interaction.response.send_message.await_args.args[0]
        assert "依頼者: **@bot** (Minecraft名: `-`)" in content
        assert "事前預け入れはありません" in content

    asyncio.run(exercise())


def test_admin_quest_menu_resolves_exact_english_names_before_confirmation() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 9001
        interaction.response.send_message = AsyncMock()
        draft = AdminQuestDraft(
            requested_query="Stone",
            requested_count=32,
            reward_query="Diamond",
            reward_count=3,
            fulfillment_hours=24,
        )

        await bot.show_admin_quest_suggestions(interaction, draft)

        view = interaction.response.send_message.await_args.kwargs["view"]
        assert isinstance(view, AdminQuestSuggestionView)
        assert view.requested_item_id == "minecraft:stone"
        assert view.reward_item_id == "minecraft:diamond"
        assert view.confirm_button.disabled is False

    asyncio.run(exercise())


def test_admin_quest_menu_requires_an_explicit_choice_for_ambiguous_search() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 9001
        interaction.response.send_message = AsyncMock()
        draft = AdminQuestDraft(
            requested_query="ネザライト",
            requested_count=1,
            reward_query="ダイヤモンド",
            reward_count=1,
            fulfillment_hours=12,
        )

        await bot.show_admin_quest_suggestions(interaction, draft)

        view = interaction.response.send_message.await_args.kwargs["view"]
        assert isinstance(view, AdminQuestSuggestionView)
        assert view.requested_item_id is None
        assert view.reward_item_id == "minecraft:diamond"
        assert view.confirm_button.disabled is True
        view.select_item("requested", "minecraft:netherite_sword")
        assert view.confirm_button.disabled is False
        assert "ネザライトの剣 x1" in view.summary()

    asyncio.run(exercise())


def test_duplicate_japanese_name_is_not_silently_resolved_to_one_item() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        options = bot._item_translator.search("レンガ")

        assert {option.item_id for option in options[:2]} == {
            "minecraft:brick",
            "minecraft:bricks",
        }
        assert bot._initial_admin_quest_item("レンガ", options) is None
        await bot.close()

    asyncio.run(exercise())


def test_admin_quest_confirmation_sends_the_selected_items_to_minecraft() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]

        async def execute(command: str) -> str:
            request_id = command.split()[-1]
            return f"USAPO_QUEST_CREATE_RESULT|1|{request_id}|42|completed|new"

        bot._execute_rcon = AsyncMock(side_effect=execute)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        draft = AdminQuestDraft(
            requested_query="石",
            requested_count=32,
            reward_query="ダイヤモンド",
            reward_count=3,
            fulfillment_hours=24,
        )

        await bot.create_admin_quest(
            interaction,
            draft,
            requested_item_id="minecraft:stone",
            reward_item_id="minecraft:diamond",
        )

        interaction.response.defer.assert_awaited_once_with()
        command = bot._execute_rcon.await_args.args[0]  # type: ignore[attr-defined]
        assert command.split()[:7] == [
            "usapo-event-bridge",
            "quest-admin-create",
            "minecraft:stone",
            "32",
            "minecraft:diamond",
            "3",
            "24",
        ]
        response = interaction.edit_original_response.await_args.kwargs
        assert "#42" in response["content"]
        assert response["view"] is None

    asyncio.run(exercise())


def test_admin_quest_retry_keeps_the_previous_form_values() -> None:
    async def exercise() -> None:
        bot = MinecraftDiscordBot(Config(discord_token="secret"))
        bot._require_server_manager = AsyncMock(return_value=True)  # type: ignore[method-assign]
        interaction = Mock(spec=discord.Interaction)
        interaction.user.id = 9001
        interaction.response.send_message = AsyncMock()
        draft = AdminQuestDraft(
            requested_query="存在しない依頼品",
            requested_count=12,
            reward_query="ダイヤモンド",
            reward_count=4,
            fulfillment_hours=36,
        )

        await bot.show_admin_quest_suggestions(interaction, draft)

        response = interaction.response.send_message.await_args.kwargs
        assert isinstance(response["view"], AdminQuestRetryView)
        modal = AdminQuestCreateModal(bot, draft)
        assert modal.requested_query.default == "存在しない依頼品"
        assert modal.requested_count.default == "12"
        assert modal.reward_query.default == "ダイヤモンド"
        assert modal.reward_count.default == "4"
        assert modal.fulfillment_hours.default == "36"
        assert "Stone" in (modal.requested_query.placeholder or "")

    asyncio.run(exercise())
