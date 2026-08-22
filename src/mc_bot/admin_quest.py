from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import discord

from mc_bot.translations import MinecraftItemOption

if TYPE_CHECKING:
    from mc_bot.bot import MinecraftDiscordBot


@dataclass(frozen=True, slots=True)
class AdminQuestDraft:
    requested_query: str
    requested_count: int
    reward_query: str
    reward_count: int
    fulfillment_hours: int


class AdminQuestCreateModal(discord.ui.Modal, title="Bot発行クエストを作る"):
    requested_query = discord.ui.TextInput(
        label="依頼品 (日本語・英語名またはID)",
        placeholder="例: 石、Stone、minecraft:stone",
        min_length=1,
        max_length=80,
    )
    requested_count = discord.ui.TextInput(
        label="依頼数",
        placeholder="例: 32",
        min_length=1,
        max_length=2,
    )
    reward_query = discord.ui.TextInput(
        label="報酬 (日本語・英語名またはID)",
        placeholder="例: ダイヤモンド、Diamond",
        min_length=1,
        max_length=80,
    )
    reward_count = discord.ui.TextInput(
        label="報酬数",
        placeholder="例: 3",
        min_length=1,
        max_length=2,
    )
    fulfillment_hours = discord.ui.TextInput(
        label="受注後の納品期限 (1〜72時間)",
        placeholder="例: 24",
        min_length=1,
        max_length=2,
    )

    def __init__(self, bot: MinecraftDiscordBot, draft: AdminQuestDraft | None = None) -> None:
        super().__init__()
        self.bot = bot
        if draft is not None:
            self.requested_query.default = draft.requested_query
            self.requested_count.default = str(draft.requested_count)
            self.reward_query.default = draft.reward_query
            self.reward_count.default = str(draft.reward_count)
            self.fulfillment_hours.default = str(draft.fulfillment_hours)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            draft = AdminQuestDraft(
                requested_query=str(self.requested_query).strip(),
                requested_count=_bounded_integer(str(self.requested_count), "依頼数", 1, 99),
                reward_query=str(self.reward_query).strip(),
                reward_count=_bounded_integer(str(self.reward_count), "報酬数", 1, 99),
                fulfillment_hours=_bounded_integer(str(self.fulfillment_hours), "納品期限", 1, 72),
            )
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.bot.show_admin_quest_suggestions(interaction, draft)


class _AdminQuestItemSelect(discord.ui.Select[discord.ui.View]):
    def __init__(
        self,
        *,
        kind: Literal["requested", "reward"],
        options: list[MinecraftItemOption],
        selected_item_id: str | None,
    ) -> None:
        self.kind = kind
        self.item_options = options
        label = "依頼品の候補を選択" if kind == "requested" else "報酬の候補を選択"
        super().__init__(
            placeholder=label,
            min_values=1,
            max_values=1,
            options=_select_options(options, selected_item_id),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, AdminQuestSuggestionView):
            await interaction.response.send_message(
                "操作画面を復元できませんでした。", ephemeral=True
            )
            return
        view.select_item(self.kind, self.values[0])
        await interaction.response.edit_message(content=view.summary(), view=view)


class _AdminQuestConfirmButton(discord.ui.Button[discord.ui.View]):
    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="この内容で公開",
            emoji="📜",
            style=discord.ButtonStyle.success,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, AdminQuestSuggestionView):
            await interaction.response.send_message(
                "操作画面を復元できませんでした。", ephemeral=True
            )
            return
        if view.requested_item_id is None or view.reward_item_id is None:
            await interaction.response.send_message(
                "依頼品と報酬を両方選択してください。", ephemeral=True
            )
            return
        await view.bot.create_admin_quest(
            interaction,
            view.draft,
            requested_item_id=view.requested_item_id,
            reward_item_id=view.reward_item_id,
        )


class _AdminQuestCancelButton(discord.ui.Button[discord.ui.View]):
    def __init__(self) -> None:
        super().__init__(label="キャンセル", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="Bot発行クエストの作成をキャンセルしました。", view=None
        )


class _AdminQuestRetryButton(discord.ui.Button[discord.ui.View]):
    def __init__(self) -> None:
        super().__init__(label="入力し直す", emoji="✏️", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, AdminQuestRetryView | AdminQuestSuggestionView):
            await interaction.response.send_message(
                "操作画面を復元できませんでした。", ephemeral=True
            )
            return
        await interaction.response.send_modal(AdminQuestCreateModal(view.bot, view.draft))


class AdminQuestRetryView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        draft: AdminQuestDraft,
        *,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.draft = draft
        self.owner_id = owner_id
        self.add_item(_AdminQuestRetryButton())
        self.add_item(_AdminQuestCancelButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この作成画面を使えるのは開いた管理者だけです。", ephemeral=True
        )
        return False


class AdminQuestSuggestionView(discord.ui.View):
    def __init__(
        self,
        bot: MinecraftDiscordBot,
        draft: AdminQuestDraft,
        *,
        owner_id: int,
        requested_options: list[MinecraftItemOption],
        reward_options: list[MinecraftItemOption],
        requested_item_id: str | None,
        reward_item_id: str | None,
    ) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.draft = draft
        self.owner_id = owner_id
        self.requested_options = requested_options
        self.reward_options = reward_options
        self.requested_item_id = requested_item_id
        self.reward_item_id = reward_item_id
        self.requested_select = _AdminQuestItemSelect(
            kind="requested",
            options=requested_options,
            selected_item_id=requested_item_id,
        )
        self.reward_select = _AdminQuestItemSelect(
            kind="reward",
            options=reward_options,
            selected_item_id=reward_item_id,
        )
        self.confirm_button = _AdminQuestConfirmButton(disabled=not self.is_complete)
        self.add_item(self.requested_select)
        self.add_item(self.reward_select)
        self.add_item(self.confirm_button)
        self.add_item(_AdminQuestRetryButton())
        self.add_item(_AdminQuestCancelButton())

    @property
    def is_complete(self) -> bool:
        return self.requested_item_id is not None and self.reward_item_id is not None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "この作成画面を使えるのは開いた管理者だけです。", ephemeral=True
        )
        return False

    def select_item(self, kind: Literal["requested", "reward"], item_id: str) -> None:
        if kind == "requested":
            if item_id not in {option.item_id for option in self.requested_options}:
                raise ValueError("unknown requested item")
            self.requested_item_id = item_id
        else:
            if item_id not in {option.item_id for option in self.reward_options}:
                raise ValueError("unknown reward item")
            self.reward_item_id = item_id
        self.requested_select.options = _select_options(
            self.requested_options, self.requested_item_id
        )
        self.reward_select.options = _select_options(self.reward_options, self.reward_item_id)
        self.confirm_button.disabled = not self.is_complete

    def summary(self) -> str:
        requested = _selected_label(self.requested_options, self.requested_item_id)
        reward = _selected_label(self.reward_options, self.reward_item_id)
        issuer = self.bot.user.mention if self.bot.user is not None else "@bot"
        prompt = (
            "依頼品と報酬を候補から選んでください。"
            if not self.is_complete
            else "内容を確認し、問題なければ公開してください。"
        )
        return (
            f"{prompt}\n\n"
            f"依頼品: **{requested} x{self.draft.requested_count}**\n"
            f"報酬: **{reward} x{self.draft.reward_count}**\n"
            f"受注後の納品期限: **{self.draft.fulfillment_hours}時間**\n"
            f"依頼者: **{issuer}** (Minecraft名: `-`)\n\n"
            "※ Bot発行のため報酬アイテムの事前預け入れはありません。\n"
            "※ 候補は各25件までです。見つからない場合は「入力し直す」で名前を絞ってください。"
        )


def _bounded_integer(value: str, label: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{label}は半角数字で入力してください。") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{label}は{minimum}〜{maximum}で入力してください。")
    return parsed


def _select_options(
    options: list[MinecraftItemOption], selected_item_id: str | None
) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=option.name[:100],
            value=option.item_id,
            description=_option_description(option),
            default=option.item_id == selected_item_id,
        )
        for option in options
    ]


def _option_description(option: MinecraftItemOption) -> str:
    suffix = f" / {option.item_id}"
    english_name = (
        option.english_name or option.item_id.removeprefix("minecraft:").replace("_", " ").title()
    )
    if len(suffix) >= 100:
        return option.item_id[:100]
    return f"{english_name[: 100 - len(suffix)]}{suffix}"


def _selected_label(options: list[MinecraftItemOption], item_id: str | None) -> str:
    if item_id is None:
        return "未選択"
    return next(
        (option.name for option in options if option.item_id == item_id),
        item_id,
    )


__all__ = [
    "AdminQuestCreateModal",
    "AdminQuestDraft",
    "AdminQuestRetryView",
    "AdminQuestSuggestionView",
]
