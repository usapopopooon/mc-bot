from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands

from mc_bot.accounts import (
    ITEM_GACHA_NOTIFICATION_RETRY_LIMIT,
    WHITELIST_RETRY_LIMIT,
    AccountStore,
    FishingComboRewardEvent,
    MinecraftAccount,
    MinecraftItemGachaDailyLimitReached,
    MinecraftItemGachaDraw,
    WoodcuttingComboRewardEvent,
)
from mc_bot.activity import ActivityKind, MinecraftActivityEvent, parse_activity_event
from mc_bot.config import Config
from mc_bot.emerald_exchange import (
    EmeraldDiamondExchangeResult,
    emerald_diamond_exchange_command,
    parse_emerald_diamond_exchange_event,
    parse_emerald_diamond_exchange_result,
)
from mc_bot.events import EventType, LogEvent, parse_log_line
from mc_bot.exchange_request import MinecraftExchangeRequest, parse_exchange_request
from mc_bot.experience import (
    ADVANCEMENT_REWARD_IN_GAME_XP,
    ADVANCEMENT_REWARD_LEVEL_BOT_SOURCE_XP,
    LevelBotXpClient,
    MinecraftResourceExchangeRequest,
    MinecraftXpExchangeRequest,
    actionbar_clear_command,
    advancement_reward_tellraw_command,
    experience_add_points_command,
    experience_query_command,
    level_up_tellraw_command,
    parse_experience_query,
    server_xp_started_tellraw_command,
    total_experience_points,
    voice_bonus_started_tellraw_command,
    voice_bonus_state_command,
    xp_exchange_tellraw_command,
)
from mc_bot.fishing import (
    FISHING_COMBO_WINDOW_SECONDS,
    fishing_combo_actionbar_command,
    fishing_combo_tellraw_command,
    is_public_fishing_milestone,
)
from mc_bot.formatting import (
    format_advancement_reward,
    format_emerald_diamond_exchange,
    format_event,
    format_fishing_combo_milestone,
    format_level_up_event,
    format_market_purchase,
    format_resource_exchange,
    format_server_announcement,
    format_server_xp_started,
    format_voice_bonus_started,
    format_woodcutting_combo_milestone,
    format_xp_exchange,
)
from mc_bot.game_messages import private_tellraw_command
from mc_bot.item_gacha import (
    ITEM_GACHA_COST_XP,
    ITEM_GACHA_DAILY_LIMIT,
    ITEM_GACHA_NORMAL_COST_XP,
    ITEM_GACHA_PREMIUM_COST_XP,
    ItemGachaCategory,
    ItemGachaKind,
    MinecraftItemGachaConfirmView,
    MinecraftItemGachaKindView,
    MinecraftItemGachaPanelView,
    draw_item_gacha_reward,
    get_item_gacha_reward,
    item_gacha_category_label,
    item_gacha_cost_xp,
    item_gacha_day,
    item_gacha_give_command,
    item_gacha_kind_label,
    item_gacha_panel_embed,
    item_gacha_result_embed,
    item_gacha_reward_categories,
    item_gacha_tellraw_command,
    item_gacha_tier_label,
)
from mc_bot.item_gacha_request import (
    MinecraftItemGachaRequest,
    parse_item_gacha_request,
)
from mc_bot.market import (
    MarketListing,
    MarketStore,
    market_listing_embed,
    market_purchase_tellraw_command,
    market_transfer_command,
    parse_market_transfer_result,
)
from mc_bot.market_request import (
    MinecraftMarketListingEvent,
    MinecraftMarketRequest,
    parse_market_listing,
    parse_market_request,
)
from mc_bot.market_ui import (
    MarketListingView,
    MarketPanelView,
    MarketPurchaseConfirmView,
    market_balance_text,
    market_guide_embed,
    market_panel_embed,
    market_purchase_confirmation_embed,
)
from mc_bot.material_buyback import (
    material_buyback_command,
    material_buyback_release_command,
    parse_material_buyback_release_result,
    parse_material_buyback_result,
)
from mc_bot.player_count import (
    PLAYER_COUNT_CHANNEL_NAME,
    PLAYER_COUNT_DISABLED_STATUS,
    parse_online_player_count,
    player_count_status,
)
from mc_bot.quest import (
    Quest,
    QuestStore,
    parse_quest_action_result,
    quest_action_command,
    quest_log_nonce,
)
from mc_bot.quest_request import MinecraftQuestStateEvent, parse_quest_state
from mc_bot.quest_ui import (
    QuestActionConfirmationView,
    QuestBackView,
    QuestListingView,
    QuestMineView,
    QuestPanelView,
    quest_action_confirmation_embed,
    quest_guide_embed,
    quest_listing_embed,
    quest_listing_has_current_controls,
    quest_log_embed,
    quest_mine_embed,
    quest_panel_embed,
)
from mc_bot.rcon import RconClient, RconError
from mc_bot.resource_shop import (
    EmeraldDiamondPackSelectView,
    MinecraftResourcePackSelectView,
    MinecraftResourceShopPanelView,
    minecraft_resource_shop_embed,
    resource_exchange_actionbar_command,
    resource_exchange_tellraw_command,
    resource_give_command,
)
from mc_bot.server_admin import (
    announcement_command,
    clean_rcon_output,
    kick_command,
    parse_online_players,
    read_cached_player_profile,
    read_cached_player_profile_by_uuid,
    read_whitelist_enabled,
    read_whitelisted_profiles,
    remove_whitelisted_player,
    upsert_whitelisted_player,
    validate_rcon_response,
)
from mc_bot.settings import RuntimeSettings, SettingsStore
from mc_bot.status_panel import (
    ServerStatusSnapshot,
    StatusPlayer,
    parse_server_list_response,
    status_panel_embed,
)
from mc_bot.tailer import LogTailer
from mc_bot.translations import AdvancementTranslator
from mc_bot.ui import (
    AccessPanelView,
    AccountSelectView,
    AdminPanelView,
    ApprovalView,
    ConfirmMinecraftIdCorrectionView,
    ConfirmRegistrationView,
    ConfirmRelinkView,
    KickPlayerSelectView,
    ServerControlView,
    VoiceControlView,
    WhitelistControlView,
    access_panel_embed,
    admin_panel_embed,
)
from mc_bot.voice import MinecraftVoicePlayer, announcement_speech_text, event_speech_text
from mc_bot.woodcutting import (
    WOODCUTTING_COMBO_WINDOW_SECONDS,
    is_public_woodcutting_milestone,
    woodcutting_actionbar_command,
    woodcutting_tellraw_command,
    woodcutting_xp_sound_command,
)
from mc_bot.xp_shop import (
    MinecraftXpPackSelectView,
    MinecraftXpShopPanelView,
    minecraft_xp_shop_embed,
    wallet_text,
)

LOGGER = logging.getLogger(__name__)
_JAVA_NAME = re.compile(r"[A-Za-z0-9_]{3,16}")
_MODERN_GAMERTAG_SUFFIX = re.compile("^(.+)[#\uff03]([0-9\uff10-\uff19]+)$")
_FULLWIDTH_DIGITS = str.maketrans(
    "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19",
    "0123456789",
)
_VOICE_CONNECTED_SPEECH = "せつぞくしました"
_VOICE_CHECK_SPEECH = "マインクラフトの読み上げは正常に動作しています"
_MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/"
_MOJANG_SESSION_PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/"
_GEYSER_XUID_URL = "https://api.geysermc.org/v2/xbox/xuid/"
_PLAYERDB_XBOX_URL = "https://playerdb.co/api/player/xbox/"
_STATUS_PANEL_REFRESH_SECONDS = 5 * 60
_VOICE_BONUS_NOTIFICATION_COOLDOWN_SECONDS = 60.0
_GAME_REQUEST_MAX_AGE = timedelta(minutes=5)
_GAME_REQUEST_CLOCK_SKEW = timedelta(minutes=1)
_MARKET_RECOVERY_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class _GameCommandUser:
    id: int


class _GameCommandFollowup:
    def __init__(self, send_message: Callable[[str], Awaitable[None]]) -> None:
        self._send_message = send_message

    async def send(self, message: str, *, ephemeral: bool = True) -> None:
        del ephemeral
        await self._send_message(message)


@dataclass(frozen=True, slots=True)
class _GameCommandInteraction:
    guild_id: int
    user: _GameCommandUser
    followup: _GameCommandFollowup


class MinecraftDiscordBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

        self._config = config
        self._translator = AdvancementTranslator.load()
        self._tailer = LogTailer(config.minecraft_log_path, config.cursor_path)
        self._settings_store = SettingsStore(config.settings_path)
        self._accounts = AccountStore(config.accounts_path)
        self._market = MarketStore(config.accounts_path)
        self._quests = QuestStore(config.accounts_path)
        self._voice_player = MinecraftVoicePlayer(
            self,
            api_url=config.voicevox_tts_api_url,
            api_token=config.voicevox_tts_api_token,
            speaker_id=config.voicevox_speaker_id,
            speed=config.voicevox_speed,
        )
        self._level_bot_xp = LevelBotXpClient(
            config.level_bot_api_url,
            config.level_bot_api_token,
        )
        self._rcon = (
            RconClient(
                config.rcon_host,
                config.rcon_port,
                config.rcon_password,
            )
            if config.rcon_password
            else None
        )
        try:
            self._settings = self._settings_store.load()
        except ValueError as error:
            LOGGER.error("Invalid settings; starting unconfigured: %s", error)
            self._settings = RuntimeSettings()
        self._settings_lock = asyncio.Lock()

        self._tailer_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._player_count_task: asyncio.Task[None] | None = None
        self._player_count_name_task: asyncio.Task[None] | None = None
        self._status_panel_task: asyncio.Task[None] | None = None
        self._minecraft_xp_task: asyncio.Task[None] | None = None
        self._activity_delivery_task: asyncio.Task[None] | None = None
        self._market_recovery_task: asyncio.Task[None] | None = None
        self._player_count_update_lock = asyncio.Lock()
        self._status_panel_update_lock = asyncio.Lock()
        self._whitelist_operation_lock = asyncio.Lock()
        self._voice_disconnect_lock = asyncio.Lock()
        self._voice_bonus_lock = asyncio.Lock()
        self._minecraft_xp_observation_lock = asyncio.Lock()
        self._activity_delivery_lock = asyncio.Lock()
        self._item_gacha_lock = asyncio.Lock()
        self._item_gacha_notification_lock = asyncio.Lock()
        self._market_lock = asyncio.Lock()
        self._market_notification_lock = asyncio.Lock()
        self._market_panel_lock = asyncio.Lock()
        self._quest_lock = asyncio.Lock()
        self._quest_panel_lock = asyncio.Lock()
        self._quest_notification_lock = asyncio.Lock()
        self._online_player_names: set[str] = set()
        self._voice_bonus_active_users: set[int] = set()
        self._voice_bonus_initialized_users: set[int] = set()
        self._voice_bonus_last_notified: dict[int, float] = {}
        self._last_player_count_status: str | None = None
        self._channel: discord.TextChannel | None = None
        self._delivery_healthy = True
        self._closing = False
        self._health_path = Path("/tmp/mc-bot-healthy")
        self._sync_ticks = 0
        self._next_status_panel_refresh_at = time.monotonic() + _STATUS_PANEL_REFRESH_SECONDS

    def _register_commands(self) -> None:
        group = app_commands.Group(
            name="mc-config",
            description="Minecraft Botの設定",
            default_permissions=discord.Permissions(manage_guild=True),
            guild_only=True,
        )
        group.command(
            name="channel",
            description="ログの通知先チャンネルを設定します",
        )(self._configure_channel)
        group.command(
            name="panel",
            description="Minecraft参加パネルを設置します",
        )(self._configure_access_panel)
        group.command(
            name="admin-panel",
            description="Minecraft管理パネルを設置します",
        )(self._configure_admin_panel)
        group.command(
            name="approval",
            description="参加登録の承認方式を設定します",
        )(self._configure_approval)
        group.command(
            name="player-count",
            description="オンライン人数チャンネルを管理します",
        )(self._configure_player_count)
        group.command(
            name="status-panel",
            description="公開Minecraftステータスパネルを設置します",
        )(self._configure_status_panel)
        group.command(
            name="xp-panel",
            description="Minecraft XP交換所パネルを設置します",
        )(self._configure_xp_shop_panel)
        group.command(
            name="resource-panel",
            description="Minecraft 資源交換所パネルを設置します",
        )(self._configure_resource_shop_panel)
        group.command(
            name="item-gacha-panel",
            description="1日3回までのMinecraftアイテムガチャパネルを設置します",
        )(self._configure_item_gacha_panel)
        group.command(
            name="market-channel",
            description="Minecraftプレイヤーマーケットの商品投稿先を設定します",
        )(self._configure_market_channel)
        group.command(
            name="market-log-channel",
            description="Minecraftプレイヤーマーケットの成約ログ投稿先を設定します",
        )(self._configure_market_log_channel)
        group.command(
            name="quest-channel",
            description="Minecraftギルド・クエストの掲示先を設定します",
        )(self._configure_quest_channel)
        group.command(
            name="quest-log-channel",
            description="Minecraftギルド・クエストの完了ログ投稿先を設定します",
        )(self._configure_quest_log_channel)
        group.command(
            name="show",
            description="現在のBot設定と稼働状態を表示します",
        )(self._show_configuration)
        self.tree.add_command(group)
        self.tree.command(
            name="vc",
            description="Minecraft読み上げのVC接続・切断を切り替えます",
        )(self._voice_command)

    async def setup_hook(self) -> None:
        await asyncio.to_thread(self._accounts.initialize)
        await asyncio.to_thread(self._market.initialize)
        await asyncio.to_thread(self._quests.initialize)
        self._voice_player.start()
        self.add_view(AccessPanelView(self))
        self.add_view(AdminPanelView(self))
        self.add_view(MinecraftXpShopPanelView(self))
        self.add_view(MinecraftResourceShopPanelView(self))
        self.add_view(MinecraftItemGachaPanelView(self))
        self.add_view(MarketPanelView(self))
        self.add_view(QuestPanelView(self))
        for listing in await asyncio.to_thread(self._market.list_open):
            if listing.discord_message_id is not None:
                self.add_view(
                    MarketListingView(
                        self,
                        listing.listing_id,
                        active=listing.status == "active",
                    ),
                    message_id=listing.discord_message_id,
                )
        for quest in await asyncio.to_thread(self._quests.list_open):
            if quest.discord_message_id is not None:
                self.add_view(
                    QuestListingView(self, quest.quest_id),
                    message_id=quest.discord_message_id,
                )
        for account in await asyncio.to_thread(self._accounts.list_pending_approvals):
            if account.approval_message_id is not None:
                self.add_view(
                    ApprovalView(self, account.id),
                    message_id=account.approval_message_id,
                )
        synced = await self.tree.sync()
        LOGGER.info("Synced %d global Discord application commands", len(synced))

    async def on_ready(self) -> None:
        await self._sync_whitelist_accounts()
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_loop(), name="health-monitor")

        await self._refresh_access_panel()
        await self._refresh_admin_panel()
        await self._refresh_xp_shop_panel()
        await self._refresh_resource_shop_panel()
        await self._refresh_item_gacha_panel()
        try:
            await self._refresh_market_panel()
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not restore market panel: %s", error)
        try:
            await self._refresh_quest_panel()
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not restore quest panel: %s", error)
        channel_id = self._settings.channel_id
        if channel_id is None:
            self._channel = None
            LOGGER.warning(
                "Notification channel is not configured; use /mc-config channel in Discord"
            )
        else:
            try:
                self._channel = await self._resolve_and_validate_channel(
                    channel_id, require_embeds=True
                )
                await self._ensure_tailer_started()
            except (OSError, RuntimeError, discord.DiscordException) as error:
                self._channel = None
                LOGGER.error(
                    "Notification forwarding is inactive; repair it with /mc-config channel: %s",
                    error,
                )
        if (
            self._settings.market_channel_id is not None
            or self._settings.quest_channel_id is not None
        ):
            try:
                await self._ensure_tailer_started()
            except (OSError, RuntimeError) as error:
                LOGGER.error("Minecraft integration log monitoring is inactive: %s", error)

        if self._settings.player_count_enabled:
            self._schedule_player_count_refresh(delay=0)
            self._schedule_player_count_name_normalization()
        self._schedule_status_panel_refresh(delay=0)
        if self._settings.voice_enabled:
            await self._restore_voice_connection()
        await self._refresh_online_player_cache()
        await self._recover_market_transactions()
        await self._recover_quests()
        self._ensure_market_recovery_started()
        self._ensure_minecraft_xp_started()
        self._ensure_activity_delivery_started()
        if self._settings.guild_id is not None:
            try:
                await self._recover_minecraft_item_gacha_notifications(self._settings.guild_id)
            except (OSError, RuntimeError, ValueError) as error:
                LOGGER.warning("Could not restore item gacha notifications: %s", error)

        LOGGER.info(
            "Discord connected as %s; loaded %d advancement translations",
            self.user,
            len(self._translator),
        )

    async def on_message(self, message: discord.Message) -> None:
        if self.user is not None and message.author.id == self.user.id:
            return
        if message.channel.id == self._settings.market_channel_id:
            try:
                await self._refresh_market_panel(move_to_bottom=True)
            except (OSError, RuntimeError, discord.DiscordException) as error:
                LOGGER.warning("Could not keep market panel at the bottom: %s", error)
        if message.channel.id == self._settings.quest_channel_id:
            try:
                await self._refresh_quest_panel(move_to_bottom=True)
            except (OSError, RuntimeError, discord.DiscordException) as error:
                LOGGER.warning("Could not keep quest panel at the bottom: %s", error)

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != self._settings.guild_id:
            return
        accounts = await asyncio.to_thread(self._accounts.list_managed_for_discord_user, member.id)
        for account in accounts:
            try:
                await self._remove_from_whitelist(account)
            except (
                OSError,
                RconError,
                RuntimeError,
                ValueError,
                discord.DiscordException,
            ) as error:
                await asyncio.to_thread(self._accounts.update_status, account.id, "pending_remove")
                LOGGER.error(
                    "Could not revoke Minecraft account %s after Discord departure: %s",
                    account.minecraft_name,
                    error,
                )

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        player_count_channel_id = self._settings.player_count_channel_id
        if self._settings.player_count_enabled and player_count_channel_id is not None:
            changed_channel_ids = {
                state.channel.id for state in (before, after) if state.channel is not None
            }
            if player_count_channel_id in changed_channel_ids:
                self._last_player_count_status = None
                self._schedule_player_count_refresh(delay=1)
        if member.guild.id == self._settings.guild_id and not member.bot:
            await self._sync_voice_bonus_for_discord_user(member.id)
        if not self._settings.voice_enabled:
            return
        async with self._voice_disconnect_lock:
            voice_client = member.guild.voice_client
            if voice_client is None or not voice_client.is_connected():
                return
            channel = voice_client.channel
            if channel is None or channel.id != self._settings.voice_channel_id:
                return
            if any(not channel_member.bot for channel_member in channel.members):
                return
            try:
                await self._save_settings(
                    replace(
                        self._settings,
                        voice_channel_id=None,
                        voice_enabled=False,
                    )
                )
                await voice_client.disconnect(force=True)
            except (OSError, discord.DiscordException) as error:
                LOGGER.warning("Could not auto-disconnect empty Minecraft voice channel: %s", error)
                return
            LOGGER.info(
                "Minecraft voice disconnected automatically from empty channel_id=%d",
                channel.id,
            )

    async def close(self) -> None:
        self._closing = True
        self._remove_health_file()
        if self._health_task is not None:
            self._health_task.cancel()
            await asyncio.gather(self._health_task, return_exceptions=True)
            self._health_task = None
        if self._player_count_task is not None:
            self._player_count_task.cancel()
            await asyncio.gather(self._player_count_task, return_exceptions=True)
            self._player_count_task = None
        if self._player_count_name_task is not None:
            self._player_count_name_task.cancel()
            await asyncio.gather(self._player_count_name_task, return_exceptions=True)
            self._player_count_name_task = None
        if self._status_panel_task is not None:
            self._status_panel_task.cancel()
            await asyncio.gather(self._status_panel_task, return_exceptions=True)
            self._status_panel_task = None
        if self._minecraft_xp_task is not None:
            self._minecraft_xp_task.cancel()
            await asyncio.gather(self._minecraft_xp_task, return_exceptions=True)
            self._minecraft_xp_task = None
        if self._activity_delivery_task is not None:
            self._activity_delivery_task.cancel()
            await asyncio.gather(self._activity_delivery_task, return_exceptions=True)
            self._activity_delivery_task = None
        if self._market_recovery_task is not None:
            self._market_recovery_task.cancel()
            await asyncio.gather(self._market_recovery_task, return_exceptions=True)
            self._market_recovery_task = None
        if self._tailer_task is not None:
            self._tailer_task.cancel()
            await asyncio.gather(self._tailer_task, return_exceptions=True)
            self._tailer_task = None
        await self._voice_player.close()
        await self._level_bot_xp.close()
        await super().close()

    @app_commands.describe(channel="通知先。省略時はコマンドを実行したチャンネル")
    async def _configure_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "通知先にはサーバーのテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            validated_channel = await self._resolve_and_validate_channel(
                target.id, require_embeds=True
            )
            await asyncio.to_thread(self._tailer.validate)
            async with self._settings_lock:
                updated = replace(
                    self._settings,
                    channel_id=target.id,
                    guild_id=target.guild.id,
                )
                await asyncio.to_thread(self._settings_store.save, updated)
                self._settings = updated
                self._channel = validated_channel
            await self._ensure_tailer_started()
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure notification channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
            return

        await interaction.followup.send(
            f"Minecraftログの通知先を {target.mention} に設定しました。",
            ephemeral=True,
        )

    @app_commands.describe(channel="参加パネルの投稿先。省略時は現在のチャンネル")
    async def _configure_access_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._configure_panel(interaction, channel, admin=False)

    @app_commands.describe(channel="管理パネルの投稿先。省略時は現在のチャンネル")
    async def _configure_admin_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._configure_panel(interaction, channel, admin=True)

    @app_commands.describe(channel="ステータスパネルの投稿先。省略時は現在のチャンネル")
    async def _configure_status_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "パネルの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            self._require_rcon()
            await self._resolve_and_validate_channel(
                target.id,
                require_embeds=True,
                require_message_history=True,
            )
            snapshot = await self._read_server_status_snapshot()
            embed = status_panel_embed(snapshot)
            old_channel_id: int | None = None
            old_message_id: int | None = None
            message: discord.Message | None = None
            async with self._status_panel_update_lock:
                old_channel_id = self._settings.status_panel_channel_id
                old_message_id = self._settings.status_panel_message_id
                if old_channel_id == target.id and old_message_id is not None:
                    try:
                        message = await target.fetch_message(old_message_id)
                        await message.edit(
                            embed=embed,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except discord.NotFound:
                        message = None
                if message is None:
                    message = await target.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                updated = replace(
                    self._settings,
                    guild_id=target.guild.id,
                    status_panel_channel_id=target.id,
                    status_panel_message_id=message.id,
                )
                await self._save_settings(updated)
            if old_message_id != message.id or old_channel_id != target.id:
                await self._delete_old_status_panel(old_channel_id, old_message_id)
        except (OSError, RuntimeError, ValueError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure status panel: %s", error)
            await interaction.followup.send(f"設置できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraftステータスパネルを {target.mention} に設置しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(channel="XP交換所パネルの投稿先。省略時は現在のチャンネル")
    async def _configure_xp_shop_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "パネルの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(
                target.id,
                require_embeds=True,
                require_message_history=True,
            )
            shop = await self._level_bot_xp.fetch_xp_shop(target.guild.id, interaction.user.id)
            if shop is None:
                raise RuntimeError("level-botのXP交換APIへ接続できません")
            old_channel_id = self._settings.xp_shop_panel_channel_id
            old_message_id = self._settings.xp_shop_panel_message_id
            message: discord.Message | None = None
            if old_channel_id == target.id and old_message_id is not None:
                try:
                    message = await target.fetch_message(old_message_id)
                    await message.edit(
                        embed=minecraft_xp_shop_embed(shop.packs),
                        view=MinecraftXpShopPanelView(self),
                    )
                except discord.NotFound:
                    message = None
            if message is None:
                message = await target.send(
                    embed=minecraft_xp_shop_embed(shop.packs),
                    view=MinecraftXpShopPanelView(self),
                )
                await self._disable_old_panel(old_channel_id, old_message_id)
            await self._save_settings(
                replace(
                    self._settings,
                    guild_id=target.guild.id,
                    xp_shop_panel_channel_id=target.id,
                    xp_shop_panel_message_id=message.id,
                )
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure Minecraft XP shop panel: %s", error)
            await interaction.followup.send(f"設置できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraft XP交換所パネルを {target.mention} に設置しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(channel="資源交換所パネルの投稿先。省略時は現在のチャンネル")
    async def _configure_resource_shop_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "パネルの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(target.id, require_embeds=True)
            shop = await self._level_bot_xp.fetch_resource_shop(
                target.guild.id, interaction.user.id
            )
            if shop is None:
                raise RuntimeError("level-botの資源交換APIへ接続できません")
            old_channel_id = self._settings.resource_shop_panel_channel_id
            old_message_id = self._settings.resource_shop_panel_message_id
            message: discord.Message | None = None
            if old_channel_id == target.id and old_message_id is not None:
                try:
                    message = await target.fetch_message(old_message_id)
                    await message.edit(
                        embed=minecraft_resource_shop_embed(shop.packs),
                        view=MinecraftResourceShopPanelView(self),
                    )
                except discord.NotFound:
                    message = None
            if message is None:
                message = await target.send(
                    embed=minecraft_resource_shop_embed(shop.packs),
                    view=MinecraftResourceShopPanelView(self),
                )
                await self._disable_old_panel(old_channel_id, old_message_id)
            await self._save_settings(
                replace(
                    self._settings,
                    guild_id=target.guild.id,
                    resource_shop_panel_channel_id=target.id,
                    resource_shop_panel_message_id=message.id,
                )
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure Minecraft resource shop panel: %s", error)
            await interaction.followup.send(f"設置できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraft 資源交換所パネルを {target.mention} に設置しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(channel="アイテムガチャパネルの投稿先。省略時は現在のチャンネル")
    async def _configure_item_gacha_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "パネルの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            self._require_rcon()
            if self._settings.channel_id is None:
                raise RuntimeError("先に /mc-config channel でログ通知先を設定してください")
            await self._resolve_and_validate_channel(target.id, require_embeds=True)
            await self._resolve_and_validate_channel(
                self._settings.channel_id,
                require_embeds=True,
            )
            old_channel_id = self._settings.item_gacha_panel_channel_id
            old_message_id = self._settings.item_gacha_panel_message_id
            message: discord.Message | None = None
            if old_channel_id == target.id and old_message_id is not None:
                try:
                    message = await target.fetch_message(old_message_id)
                    await message.edit(
                        embed=item_gacha_panel_embed(),
                        view=MinecraftItemGachaPanelView(self),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.NotFound:
                    message = None
            if message is None:
                message = await target.send(
                    embed=item_gacha_panel_embed(),
                    view=MinecraftItemGachaPanelView(self),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self._disable_old_panel(old_channel_id, old_message_id)
            await self._save_settings(
                replace(
                    self._settings,
                    guild_id=target.guild.id,
                    item_gacha_panel_channel_id=target.id,
                    item_gacha_panel_message_id=message.id,
                )
            )
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure Minecraft item gacha panel: %s", error)
            await interaction.followup.send(f"設置できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraftアイテムガチャパネルを {target.mention} に設置しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(channel="商品カードの投稿先。省略時は現在のチャンネル")
    async def _configure_market_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "商品投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(
                target.id,
                require_embeds=True,
                require_message_history=True,
            )
            old_channel_id = self._settings.market_channel_id
            old_message_id = self._settings.market_panel_message_id
            await self._save_settings(
                replace(
                    self._settings,
                    guild_id=target.guild.id,
                    market_channel_id=target.id,
                    market_panel_message_id=(
                        old_message_id if old_channel_id == target.id else None
                    ),
                )
            )
            for listing in await asyncio.to_thread(self._market.list_open):
                if listing.discord_message_id is None:
                    await self._post_market_listing(listing, move_panel=False)
                else:
                    await self._refresh_market_listing(listing.listing_id, move_panel=False)
            await self._refresh_market_panel(move_to_bottom=True)
            if old_channel_id != target.id:
                await self._disable_old_panel(old_channel_id, old_message_id)
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure Minecraft market channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraftプレイヤーマーケットを {target.mention} に設定しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(channel="フリマ成約ログの投稿先。省略時は現在のチャンネル")
    async def _configure_market_log_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "フリマ成約ログの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(target.id, require_embeds=True)
            await self._save_settings(
                replace(
                    self._settings,
                    guild_id=target.guild.id,
                    market_log_channel_id=target.id,
                )
            )
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure Minecraft market log channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraftフリマ成約ログを {target.mention} に設定しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(channel="クエストカードの投稿先。省略時は現在のチャンネル")
    async def _configure_quest_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "クエスト掲示先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(
                target.id,
                require_embeds=True,
                require_message_history=True,
            )
            await asyncio.to_thread(self._tailer.validate)
            old_channel_id = self._settings.quest_channel_id
            old_panel_id = self._settings.quest_panel_message_id
            if old_channel_id is not None and old_channel_id != target.id:
                for quest in await asyncio.to_thread(self._quests.list_open):
                    await self._delete_quest_card(quest, channel_id=old_channel_id)
            await self._save_settings(
                replace(
                    self._settings,
                    guild_id=target.guild.id,
                    quest_channel_id=target.id,
                    quest_panel_message_id=(old_panel_id if old_channel_id == target.id else None),
                )
            )
            await self._ensure_tailer_started()
            for quest in await asyncio.to_thread(self._quests.list_open):
                await self._refresh_quest_listing(quest.quest_id, move_panel=False)
            await self._refresh_quest_panel(move_to_bottom=True)
            if old_channel_id != target.id:
                await self._disable_old_panel(old_channel_id, old_panel_id)
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure Minecraft quest channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraftギルド・クエスト掲示板を {target.mention} に設定しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(channel="クエスト完了・終了ログの投稿先。省略時は現在のチャンネル")
    async def _configure_quest_log_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "クエストログの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(
                target.id,
                require_embeds=True,
                require_message_history=True,
            )
            await self._save_settings(
                replace(
                    self._settings,
                    guild_id=target.guild.id,
                    quest_log_channel_id=target.id,
                )
            )
            await self._deliver_quest_logs()
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure Minecraft quest log channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
            return
        await interaction.followup.send(
            f"Minecraftクエスト完了ログを {target.mention} に設定しました。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.describe(
        mode="自動承認または管理者承認",
        channel="管理者承認時に申請を投稿するチャンネル",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="自動承認", value="automatic"),
            app_commands.Choice(name="管理者承認", value="manual"),
        ]
    )
    async def _configure_approval(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        approval_channel_id = self._settings.approval_channel_id
        if mode.value == "manual":
            try:
                target = channel
                if target is None and approval_channel_id is not None:
                    target = await self._resolve_and_validate_channel(
                        approval_channel_id, require_embeds=True
                    )
                if target is None:
                    await interaction.followup.send(
                        "管理者承認では申請の投稿先チャンネルを指定してください。",
                        ephemeral=True,
                    )
                    return
                self._ensure_same_guild(target.guild.id)
                await self._resolve_and_validate_channel(target.id, require_embeds=True)
            except (RuntimeError, discord.DiscordException) as error:
                await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)
                return
            approval_channel_id = target.id

        updated = replace(
            self._settings,
            guild_id=interaction.guild_id,
            approval_mode=mode.value,
            approval_channel_id=approval_channel_id,
        )
        await self._save_settings(updated)
        await self._refresh_access_panel()
        label = "自動承認" if mode.value == "automatic" else "管理者承認"
        await interaction.followup.send(f"承認方式を「{label}」に設定しました。", ephemeral=True)

    @app_commands.describe(action="人数表示チャンネルに対する操作")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="有効化", value="enable"),
            app_commands.Choice(name="更新停止", value="disable"),
            app_commands.Choice(name="チャンネル削除", value="remove"),
        ]
    )
    async def _configure_player_count(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True)

        try:
            self._ensure_same_guild(guild.id)
            if action.value == "enable":
                channel = await self._enable_player_count_channel(interaction, guild)
                await interaction.followup.send(
                    f"オンライン人数表示を {channel.mention} で開始しました。",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif action.value == "disable":
                channel = await self._get_player_count_channel(guild)
                updated = replace(
                    self._settings,
                    guild_id=guild.id,
                    player_count_enabled=False,
                )
                await self._save_settings(updated)
                if channel is not None:
                    async with self._player_count_update_lock:
                        await channel.edit(
                            status=PLAYER_COUNT_DISABLED_STATUS,
                            reason="Minecraftオンライン人数表示を停止",
                        )
                        self._last_player_count_status = PLAYER_COUNT_DISABLED_STATUS
                await interaction.followup.send(
                    "オンライン人数の更新を停止しました。",
                    ephemeral=True,
                )
            else:
                channel = await self._get_player_count_channel(guild)
                if channel is not None:
                    await channel.delete(reason="Minecraftオンライン人数チャンネルを削除")
                updated = replace(
                    self._settings,
                    guild_id=guild.id,
                    player_count_channel_id=None,
                    player_count_enabled=False,
                )
                await self._save_settings(updated)
                await interaction.followup.send(
                    "オンライン人数チャンネルを削除しました。",
                    ephemeral=True,
                )
        except (OSError, RuntimeError, ValueError, discord.DiscordException) as error:
            LOGGER.warning("Could not configure player count channel: %s", error)
            await interaction.followup.send(f"設定できませんでした: {error}", ephemeral=True)

    async def _show_configuration(self, interaction: discord.Interaction) -> None:
        if not await self._require_server_manager(interaction):
            return
        forwarding = (
            self._channel is not None
            and self._tailer_task is not None
            and not self._tailer_task.done()
            and self._delivery_healthy
        )
        registered, unlinked, pending = await asyncio.to_thread(self._accounts.count_summary)
        mode = "自動承認" if self._settings.approval_mode == "automatic" else "管理者承認"
        await interaction.response.send_message(
            "\n".join(
                (
                    f"ログ通知先: {self._channel_text(self._settings.channel_id)}",
                    f"参加パネル: {self._channel_text(self._settings.panel_channel_id)}",
                    f"管理パネル: {self._channel_text(self._settings.admin_panel_channel_id)}",
                    "ステータスパネル: "
                    f"{self._channel_text(self._settings.status_panel_channel_id)}",
                    "XP交換所パネル: "
                    f"{self._channel_text(self._settings.xp_shop_panel_channel_id)}",
                    "資源交換所パネル: "
                    f"{self._channel_text(self._settings.resource_shop_panel_channel_id)}",
                    "アイテムガチャパネル: "
                    f"{self._channel_text(self._settings.item_gacha_panel_channel_id)}",
                    f"フリマ商品チャンネル: {self._channel_text(self._settings.market_channel_id)}",
                    f"フリマ成約ログ: {self._channel_text(self._settings.market_log_channel_id)}",
                    f"クエスト掲示板: {self._channel_text(self._settings.quest_channel_id)}",
                    f"クエスト完了ログ: {self._channel_text(self._settings.quest_log_channel_id)}",
                    f"承認方式: {mode}",
                    f"申請確認先: {self._channel_text(self._settings.approval_channel_id)}",
                    "人数表示: "
                    f"{self._channel_text(self._settings.player_count_channel_id)} "
                    f"({'稼働中' if self._settings.player_count_enabled else '停止中'})",
                    "Minecraft読み上げ: "
                    f"{self._channel_text(self._settings.voice_channel_id)} "
                    f"({'稼働中' if self._settings.voice_enabled else '停止中'})",
                    f"ログ転送: {'稼働中' if forwarding else '停止中'}",
                    f"登録: {registered}件 (未連携 {unlinked}件、承認待ち {pending}件)",
                    f"RCON: {'設定済み' if self._rcon is not None else '未設定'}",
                )
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _configure_panel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
        *,
        admin: bool,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "パネルの投稿先にはテキストチャンネルを指定してください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            self._ensure_same_guild(target.guild.id)
            await self._resolve_and_validate_channel(target.id, require_embeds=True)
            old_channel_id = (
                self._settings.admin_panel_channel_id if admin else self._settings.panel_channel_id
            )
            old_message_id = (
                self._settings.admin_panel_message_id if admin else self._settings.panel_message_id
            )
            embed = (
                admin_panel_embed() if admin else access_panel_embed(self._settings.approval_mode)
            )
            view: discord.ui.View = AdminPanelView(self) if admin else AccessPanelView(self)
            message: discord.Message | None = None
            if old_channel_id == target.id and old_message_id is not None:
                try:
                    message = await target.fetch_message(old_message_id)
                    await message.edit(embed=embed, view=view)
                except discord.NotFound:
                    message = None
            if message is None:
                message = await target.send(embed=embed, view=view)
                await self._disable_old_panel(old_channel_id, old_message_id)

            fields = (
                {
                    "admin_panel_channel_id": target.id,
                    "admin_panel_message_id": message.id,
                }
                if admin
                else {
                    "panel_channel_id": target.id,
                    "panel_message_id": message.id,
                }
            )
            updated = replace(
                self._settings,
                guild_id=target.guild.id,
                **fields,
            )
            await self._save_settings(updated)
        except (RuntimeError, discord.DiscordException) as error:
            await interaction.followup.send(f"設置できませんでした: {error}", ephemeral=True)
            return
        name = "管理パネル" if admin else "参加パネル"
        await interaction.followup.send(
            f"{name}を {target.mention} に設置しました。", ephemeral=True
        )

    async def validate_panel_interaction(
        self, interaction: discord.Interaction, *, admin: bool
    ) -> bool:
        expected_message_id = (
            self._settings.admin_panel_message_id if admin else self._settings.panel_message_id
        )
        if interaction.message is None or interaction.message.id != expected_message_id:
            await interaction.response.send_message(
                "このパネルは現在使用されていません。最新のパネルをご利用ください。",
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self._settings.guild_id:
            await interaction.response.send_message(
                "このDiscordサーバーでは利用できません。", ephemeral=True
            )
            return False
        if admin:
            return await self._require_server_manager(interaction)
        if not isinstance(interaction.user, discord.Member) or interaction.user.bot:
            await interaction.response.send_message(
                "Discordサーバーのメンバーだけが利用できます。", ephemeral=True
            )
            return False
        return True

    async def validate_xp_shop_panel(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.message is None
            or interaction.message.id != self._settings.xp_shop_panel_message_id
        ):
            await interaction.response.send_message(
                "このパネルは現在使用されていません。最新のパネルをご利用ください。",
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self._settings.guild_id:
            await interaction.response.send_message(
                "このDiscordサーバーでは利用できません。", ephemeral=True
            )
            return False
        if not isinstance(interaction.user, discord.Member) or interaction.user.bot:
            await interaction.response.send_message(
                "Discordサーバーのメンバーだけが利用できます。", ephemeral=True
            )
            return False
        return True

    async def show_minecraft_xp_shop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        shop = await self._level_bot_xp.fetch_xp_shop(interaction.guild_id, interaction.user.id)
        if shop is None:
            await interaction.followup.send(
                "XP交換所を取得できませんでした。少し待ってから再度お試しください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            (
                f"交換可能XP: **{shop.wallet.available_xp:,} XP**\n"
                "交換内容を選んでください。"
                "Minecraftサーバーへの参加中のみ交換できます。"
            ),
            view=MinecraftXpPackSelectView(self, owner_id=interaction.user.id, shop=shop),
            ephemeral=True,
        )

    async def show_minecraft_xp_balance(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        shop = await self._level_bot_xp.fetch_xp_shop(interaction.guild_id, interaction.user.id)
        if shop is None:
            await interaction.followup.send(
                "XP残高を取得できませんでした。少し待ってから再度お試しください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(wallet_text(shop.wallet), ephemeral=True)

    async def confirm_minecraft_xp_exchange(
        self,
        interaction: discord.Interaction,
        *,
        request_id: str,
        cost_xp: int,
        expected_reward_xp: int,
    ) -> MinecraftXpExchangeRequest | None:
        if interaction.guild_id is None:
            return None
        return await self._level_bot_xp.request_xp_exchange(
            interaction.guild_id,
            interaction.user.id,
            request_id,
            cost_xp,
            expected_reward_xp,
        )

    async def validate_resource_shop_panel(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.message is None
            or interaction.message.id != self._settings.resource_shop_panel_message_id
        ):
            await interaction.response.send_message(
                "このパネルは現在使用されていません。最新のパネルをご利用ください。",
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self._settings.guild_id:
            await interaction.response.send_message(
                "このDiscordサーバーでは利用できません。", ephemeral=True
            )
            return False
        if not isinstance(interaction.user, discord.Member) or interaction.user.bot:
            await interaction.response.send_message(
                "Discordサーバーのメンバーだけが利用できます。", ephemeral=True
            )
            return False
        return True

    async def validate_item_gacha_panel(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.message is None
            or interaction.message.id != self._settings.item_gacha_panel_message_id
        ):
            await interaction.response.send_message(
                "このパネルは現在使用されていません。最新のパネルをご利用ください。",
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self._settings.guild_id:
            await interaction.response.send_message(
                "このDiscordサーバーでは利用できません。", ephemeral=True
            )
            return False
        if not isinstance(interaction.user, discord.Member) or interaction.user.bot:
            await interaction.response.send_message(
                "Discordサーバーのメンバーだけが利用できます。", ephemeral=True
            )
            return False
        return True

    async def show_minecraft_item_gacha_kind_selection(
        self,
        interaction: discord.Interaction,
        category: ItemGachaCategory,
    ) -> None:
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{item_gacha_category_label(category)}ガチャ",
                description=(
                    "ランク確率と1日の回数上限は、どの種類を選んでも共通です。"
                    "引き方を選んでください。"
                ),
                color=discord.Color.gold(),
            ),
            view=MinecraftItemGachaKindView(
                self,
                owner_id=interaction.user.id,
                category=category,
            ),
            ephemeral=True,
        )

    async def show_minecraft_item_gacha_confirmation(
        self,
        interaction: discord.Interaction,
        draw_kind: ItemGachaKind,
        draw_category: ItemGachaCategory = "all",
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            account, reason = await self._online_exchange_account(interaction.user.id)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not verify player for item gacha preview: %s", error)
            await interaction.followup.send(
                "Minecraftサーバーへの参加状況を確認できませんでした。"
                "少し待ってから再度お試しください。",
                ephemeral=True,
            )
            return
        if account is None:
            message = (
                "連携したMinecraftアカウントが複数同時にオンラインです。"
                "受取先にする1アカウントだけで参加してから再度お試しください。"
                if reason == "account_ambiguous"
                else "連携したMinecraftアカウントでサーバーに参加してからご利用ください。"
            )
            await interaction.followup.send(message, ephemeral=True)
            return

        draw_day = item_gacha_day(datetime.now(UTC))
        latest_draw = await asyncio.to_thread(
            self._accounts.get_minecraft_item_gacha_draw,
            guild_id=interaction.guild_id,
            discord_user_id=interaction.user.id,
            draw_day=draw_day,
        )
        draw_count = await asyncio.to_thread(
            self._accounts.count_minecraft_item_gacha_draws,
            guild_id=interaction.guild_id,
            discord_user_id=interaction.user.id,
            draw_day=draw_day,
        )
        retrying = latest_draw is not None and latest_draw.status in {"reserved", "retryable"}
        if draw_count >= ITEM_GACHA_DAILY_LIMIT and not retrying:
            await interaction.followup.send(
                f"本日のアイテムガチャは **{ITEM_GACHA_DAILY_LIMIT}回**すべて引き終えています。"
                "次は日本時間0:00から引けます。",
                ephemeral=True,
            )
            return
        effective_kind: ItemGachaKind = draw_kind
        effective_category: ItemGachaCategory = draw_category
        retry_note = ""
        if retrying and latest_draw is not None:
            effective_kind = "premium" if latest_draw.draw_kind == "premium" else "normal"
            effective_category = cast(ItemGachaCategory, latest_draw.draw_category)
            retry_note = (
                f"\n未完了の**{item_gacha_category_label(effective_category)}・"
                f"{item_gacha_kind_label(effective_kind)}ガチャ**"
                f" (本日{latest_draw.draw_number}回目) を同じ景品で再開します。"
            )

        offer = await self._level_bot_xp.fetch_item_gacha_offer(
            interaction.guild_id, interaction.user.id
        )
        if (
            offer is None
            or offer.cost_xp != ITEM_GACHA_NORMAL_COST_XP
            or offer.normal_cost_xp != ITEM_GACHA_NORMAL_COST_XP
            or offer.premium_cost_xp != ITEM_GACHA_PREMIUM_COST_XP
            or offer.daily_limit != ITEM_GACHA_DAILY_LIMIT
        ):
            await interaction.followup.send(
                "ガチャのXP残高または価格を確認できませんでした。"
                "少し待ってから再度お試しください。",
                ephemeral=True,
            )
            return
        cost_xp = item_gacha_cost_xp(effective_kind)
        affordable = retrying or offer.wallet.available_xp >= cost_xp
        embed = discord.Embed(
            title=(
                f"{item_gacha_category_label(effective_category)}・"
                f"{item_gacha_kind_label(effective_kind)}ガチャの確認"
            ),
            description=(
                f"サーバーXP **{cost_xp:,} XP**を使って、"
                f"本日 **{draw_count + (0 if retrying else 1)}/{ITEM_GACHA_DAILY_LIMIT}回目**"
                "のアイテムガチャを引きます。\n"
                f"景品の内容は確定するまで秘密です。{retry_note}"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="現在のXP",
            value=f"{offer.wallet.available_xp:,} XP",
            inline=True,
        )
        embed.add_field(
            name="抽選後",
            value=(
                "決済状態を再確認します"
                if retrying
                else (
                    f"{offer.wallet.available_xp - cost_xp:,} XP"
                    if affordable
                    else "XPが不足しています"
                )
            ),
            inline=True,
        )
        embed.add_field(
            name="受け取り",
            value=(
                "Minecraftが景品の配布を明確に拒否した場合、XP予約を取り消します。"
                "通信断で成否を確認できない場合は、二重配布防止のため管理者確認になります。"
            ),
            inline=False,
        )
        await interaction.followup.send(
            embed=embed,
            view=MinecraftItemGachaConfirmView(
                self,
                owner_id=interaction.user.id,
                draw_kind=effective_kind,
                draw_category=effective_category,
                cost_xp=cost_xp,
                affordable=affordable,
            ),
            ephemeral=True,
        )

    async def draw_minecraft_item_gacha(
        self,
        interaction: discord.Interaction,
        *,
        draw_kind: ItemGachaKind = "normal",
        draw_category: ItemGachaCategory = "all",
        expected_cost_xp: int = ITEM_GACHA_COST_XP,
        response_ready: bool = False,
        request_id: str | None = None,
        known_account: MinecraftAccount | None = None,
        draw_time: datetime | None = None,
    ) -> None:
        if interaction.guild_id is None:
            sender = (
                interaction.followup.send if response_ready else interaction.response.send_message
            )
            await sender("Discordサーバー内で利用してください。", ephemeral=True)
            return
        if not response_ready:
            await interaction.response.defer(ephemeral=True, thinking=True)
        if expected_cost_xp != item_gacha_cost_xp(draw_kind):
            await interaction.followup.send(
                "ガチャ価格が更新されました。パネルから開き直してください。",
                ephemeral=True,
            )
            return
        async with self._item_gacha_lock:
            account = known_account
            reason = None
            if account is None:
                try:
                    account, reason = await self._online_exchange_account(interaction.user.id)
                except (OSError, RconError, RuntimeError, ValueError) as error:
                    LOGGER.warning("Could not verify player for item gacha: %s", error)
                    await interaction.followup.send(
                        "Minecraftサーバーへの参加状況を確認できませんでした。"
                        "少し待ってから再度お試しください。",
                        ephemeral=True,
                    )
                    return
            if account is None:
                message = (
                    "連携したMinecraftアカウントが複数同時にオンラインです。"
                    "受取先にする1アカウントだけで参加してから再度お試しください。"
                    if reason == "account_ambiguous"
                    else "連携したMinecraftアカウントでサーバーに参加してからご利用ください。"
                )
                await interaction.followup.send(message, ephemeral=True)
                return

            reward = draw_item_gacha_reward(draw_kind, category=draw_category)
            draw_day = item_gacha_day(draw_time or datetime.now(UTC))
            try:
                draw, created = await asyncio.to_thread(
                    self._accounts.reserve_minecraft_item_gacha_draw,
                    draw_id=request_id or str(uuid.uuid4()),
                    guild_id=interaction.guild_id,
                    discord_user_id=interaction.user.id,
                    account_id=account.id,
                    player_name=account.server_player_name,
                    draw_day=draw_day,
                    draw_kind=draw_kind,
                    draw_category=draw_category,
                    cost_xp=expected_cost_xp,
                    tier=reward.tier,
                    reward_key=reward.key,
                    item_spec=reward.item_spec,
                    item_name=reward.item_name,
                    item_count=reward.item_count,
                )
            except MinecraftItemGachaDailyLimitReached:
                await interaction.followup.send(
                    f"本日のアイテムガチャは **{ITEM_GACHA_DAILY_LIMIT}回**すべて引き終えています。"
                    "次は日本時間0:00から引けます。",
                    ephemeral=True,
                )
                return
            except (OSError, RuntimeError, ValueError) as error:
                LOGGER.error("Could not reserve Minecraft item gacha draw: %s", error)
                await interaction.followup.send(
                    "抽選を開始できませんでした。少し待ってから再度お試しください。",
                    ephemeral=True,
                )
                return

            if (
                draw.draw_kind != draw_kind
                or draw.draw_category != draw_category
                or draw.cost_xp != expected_cost_xp
            ):
                pending_kind: ItemGachaKind = "premium" if draw.draw_kind == "premium" else "normal"
                pending_argument = "rare" if pending_kind == "premium" else "normal"
                pending_category: ItemGachaCategory = cast(ItemGachaCategory, draw.draw_category)
                category_argument = {
                    "all": "",
                    "resources": "resource ",
                    "adventure": "adventure ",
                    "equipment": "equipment ",
                }[pending_category]
                await interaction.followup.send(
                    f"未完了の{item_gacha_category_label(pending_category)}・"
                    f"{item_gacha_kind_label(pending_kind)}ガチャ"
                    f" ({draw.cost_xp:,} XP) があります。今回はXPを使っていません。"
                    f"/gacha {category_argument}{pending_argument} "
                    "で同じ景品の抽選を再開してください。",
                    ephemeral=True,
                )
                return

            if created:
                # ``reserved`` はRCON送信直前以降の不確定区間だけに使う。
                # XP API待ちの間に停止しても、安全に同じ抽選を再開できるようにする。
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_item_gacha_status,
                    draw.draw_id,
                    "retryable",
                )

            if draw.status == "delivered":
                await self._level_bot_xp.update_item_gacha_spend(
                    request_id=draw.draw_id,
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                    action="complete",
                )
                await interaction.followup.send(
                    self._item_gacha_received_text(draw, already=True),
                    ephemeral=True,
                )
                return
            if draw.status == "ambiguous":
                await self._level_bot_xp.update_item_gacha_spend(
                    request_id=draw.draw_id,
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                    action="complete",
                )
                await interaction.followup.send(
                    "本日の抽選は処理済みですが、景品の配布結果を確認できませんでした。"
                    "二重配布を避けるため再抽選は行いません。管理者へご連絡ください。",
                    ephemeral=True,
                )
                return
            if draw.account_id != account.id:
                await interaction.followup.send(
                    f"本日の景品は **{discord.utils.escape_markdown(draw.player_name)}** さん宛てに"
                    "確定しています。そのアカウントで参加してから再度お試しください。",
                    ephemeral=True,
                )
                return
            if not self._item_gacha_draw_matches_catalog(draw):
                if draw.status == "retryable":
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_item_gacha_status,
                        draw.draw_id,
                        "reserved",
                    )
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_item_gacha_status,
                    draw.draw_id,
                    "ambiguous",
                )
                LOGGER.error("Minecraft item gacha catalog changed for draw=%s", draw.draw_id)
                await interaction.followup.send(
                    "本日の景品データを安全に確認できませんでした。管理者へご連絡ください。",
                    ephemeral=True,
                )
                return
            if not created and draw.status == "reserved":
                updated_at = datetime.fromisoformat(draw.updated_at)
                if datetime.now(UTC) - updated_at < timedelta(seconds=60):
                    await interaction.followup.send(
                        "本日の抽選を処理中です。少し待ってから結果をご確認ください。",
                        ephemeral=True,
                    )
                    return
                # 前回プロセスが付与前後に停止した可能性があり、安全な再送判定ができない。
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_item_gacha_status,
                    draw.draw_id,
                    "ambiguous",
                )
                await self._level_bot_xp.update_item_gacha_spend(
                    request_id=draw.draw_id,
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                    action="complete",
                )
                await interaction.followup.send(
                    "本日の抽選は処理済みですが、景品の配布結果を確認できませんでした。"
                    "二重配布を避けるため再抽選は行いません。管理者へご連絡ください。",
                    ephemeral=True,
                )
                return
            spend = await self._level_bot_xp.request_item_gacha_spend(
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                request_id=draw.draw_id,
                account_id=draw.account_id,
                draw_day=draw.draw_day,
                draw_category=draw.draw_category,
                expected_cost_xp=draw.cost_xp,
            )
            if spend is None:
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_item_gacha_status,
                    draw.draw_id,
                    "retryable",
                )
                await interaction.followup.send(
                    "XP決済を確認できなかったため、景品は配布していません。"
                    "少し待ってからパネルより再度お試しください。",
                    ephemeral=True,
                )
                return
            if spend.cost_xp != draw.cost_xp:
                cancelled = await self._level_bot_xp.update_item_gacha_spend(
                    request_id=draw.draw_id,
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                    action="cancel",
                )
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_item_gacha_status,
                    draw.draw_id,
                    "retryable",
                )
                LOGGER.error(
                    "Minecraft item gacha spend price mismatch draw=%s expected=%d actual=%d "
                    "cancelled=%s",
                    draw.draw_id,
                    draw.cost_xp,
                    spend.cost_xp,
                    cancelled,
                )
                await interaction.followup.send(
                    "XP決済の価格が一致しなかったため、景品は配布していません。"
                    "少し待ってからパネルより再度お試しください。",
                    ephemeral=True,
                )
                return
            if spend.status != "reserved":
                if spend.status == "completed":
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_item_gacha_status,
                        draw.draw_id,
                        "reserved",
                    )
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_item_gacha_status,
                        draw.draw_id,
                        "ambiguous",
                    )
                    message = (
                        "XP決済は確定済みですが、景品の配布状態を安全に確認できません。"
                        "二重配布を避けるため管理者へご連絡ください。"
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_item_gacha_status,
                        draw.draw_id,
                        "retryable",
                    )
                    message = spend.message
                await interaction.followup.send(message, ephemeral=True)
                return

            await asyncio.to_thread(
                self._accounts.mark_minecraft_item_gacha_status,
                draw.draw_id,
                "reserved",
            )

            try:
                await self._execute_checked_rcon(
                    item_gacha_give_command(draw.player_name, draw.reward_key)
                )
            except ValueError as error:
                cancelled = await self._level_bot_xp.update_item_gacha_spend(
                    request_id=draw.draw_id,
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                    action="cancel",
                )
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_item_gacha_status,
                    draw.draw_id,
                    "retryable",
                )
                LOGGER.warning(
                    "Minecraft rejected item gacha delivery draw=%s refund=%s: %s",
                    draw.draw_id,
                    cancelled,
                    error,
                )
                await interaction.followup.send(
                    "景品は確定しましたが、Minecraftが配布を受け付けませんでした。"
                    "本日の同じ景品で再試行できます。少し待ってからもう一度押してください。",
                    ephemeral=True,
                )
                return
            except (OSError, RconError, RuntimeError) as error:
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_item_gacha_status,
                    draw.draw_id,
                    "ambiguous",
                )
                LOGGER.warning(
                    "Minecraft item gacha delivery became ambiguous draw=%s: %s",
                    draw.draw_id,
                    error,
                )
                await self._level_bot_xp.update_item_gacha_spend(
                    request_id=draw.draw_id,
                    guild_id=interaction.guild_id,
                    user_id=interaction.user.id,
                    action="complete",
                )
                await interaction.followup.send(
                    "景品の配布結果を確認できませんでした。二重配布を避けるため"
                    "再抽選は行いません。アイテム欄を確認し、見当たらなければ管理者へ"
                    "ご連絡ください。",
                    ephemeral=True,
                )
                return

            await asyncio.to_thread(
                self._accounts.mark_minecraft_item_gacha_status,
                draw.draw_id,
                "delivered",
            )
            completed = await self._level_bot_xp.update_item_gacha_spend(
                request_id=draw.draw_id,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                action="complete",
            )
            if not completed:
                LOGGER.warning(
                    "Could not finalize item gacha XP spend draw=%s",
                    draw.draw_id,
                )
            await self._flush_minecraft_item_gacha_notifications(interaction.guild_id)
            await interaction.followup.send(
                self._item_gacha_received_text(draw, already=False),
                ephemeral=True,
            )

    async def _handle_minecraft_item_gacha_request(
        self,
        request: MinecraftItemGachaRequest,
    ) -> None:
        requested_at = self._fresh_game_request_time(request.requested_at)
        if requested_at is None:
            await self._send_minecraft_private_message(
                request.player_name,
                "このガチャ要求は期限切れです。もう一度 /gacha を実行してください。",
            )
            return
        guild_id = self._settings.guild_id
        if (
            guild_id is None
            or not self._config.level_bot_api_url
            or not self._config.level_bot_api_token
            or self._rcon is None
        ):
            await self._send_minecraft_private_message(
                request.player_name,
                "アイテムガチャは現在準備中です。少し待ってからお試しください。",
            )
            return
        current_cost_xp = item_gacha_cost_xp(request.draw_kind)
        if request.expected_cost_xp != current_cost_xp:
            await self._send_minecraft_private_message(
                request.player_name,
                "ガチャ料金が更新されました。今回はXPを使っていません。"
                "Minecraftサーバー更新後にもう一度 /gacha を実行してください。",
            )
            return
        try:
            account = await asyncio.to_thread(
                self._accounts.get_by_player_uuid,
                request.player_uuid,
            )
        except ValueError as error:
            LOGGER.error(
                "Could not identify item gacha account request=%s player_uuid=%s: %s",
                request.request_id,
                request.player_uuid,
                error,
            )
            await self._send_minecraft_private_message(
                request.player_name,
                "アカウント連携を安全に確認できませんでした。管理者へご連絡ください。",
            )
            return
        if account is None or account.status != "active" or account.discord_user_id is None:
            await self._send_minecraft_private_message(
                request.player_name,
                "アイテムガチャにはDiscordアカウントとの連携が必要です。",
            )
            return

        # Paperが記録したUUIDを本人確認に使い、現在オンラインの実名を付与先にする。
        delivery_account = replace(account, server_player_name=request.player_name)
        interaction = _GameCommandInteraction(
            guild_id=guild_id,
            user=_GameCommandUser(account.discord_user_id),
            followup=_GameCommandFollowup(
                lambda message: self._send_minecraft_private_message(
                    request.player_name,
                    message,
                )
            ),
        )
        await self.draw_minecraft_item_gacha(
            interaction,  # type: ignore[arg-type]
            draw_kind=request.draw_kind,
            draw_category=request.draw_category,
            expected_cost_xp=current_cost_xp,
            response_ready=True,
            request_id=request.request_id,
            known_account=delivery_account,
            draw_time=requested_at,
        )

    async def _handle_minecraft_exchange_request(
        self,
        request: MinecraftExchangeRequest,
    ) -> None:
        if (
            request.kind != "material_buyback"
            and self._fresh_game_request_time(request.requested_at) is None
        ):
            await self._send_minecraft_private_message(
                request.player_name,
                "この交換要求は期限切れです。もう一度 /exchange を実行してください。",
            )
            return
        guild_id = self._settings.guild_id
        if guild_id is None or self._rcon is None:
            await self._release_material_buyback_if_needed(request)
            await self._send_minecraft_private_message(
                request.player_name,
                "交換所は現在準備中です。少し待ってからお試しください。",
            )
            return
        if request.kind != "emerald_diamond" and (
            not self._config.level_bot_api_url or not self._config.level_bot_api_token
        ):
            await self._release_material_buyback_if_needed(request)
            await self._send_minecraft_private_message(
                request.player_name,
                "交換所は現在準備中です。少し待ってからお試しください。",
            )
            return
        try:
            account = await asyncio.to_thread(
                self._accounts.get_by_player_uuid,
                request.player_uuid,
            )
        except ValueError as error:
            LOGGER.error(
                "Could not identify exchange account request=%s player_uuid=%s: %s",
                request.request_id,
                request.player_uuid,
                error,
            )
            await self._release_material_buyback_if_needed(request)
            await self._send_minecraft_private_message(
                request.player_name,
                "アカウント連携を安全に確認できませんでした。管理者へご連絡ください。",
            )
            return
        if account is None or account.status != "active" or account.discord_user_id is None:
            await self._release_material_buyback_if_needed(request)
            await self._send_minecraft_private_message(
                request.player_name,
                "交換所の利用にはDiscordアカウントとの連携が必要です。",
            )
            return

        # Paperが記録したUUIDを本人確認に使い、非同期配布でも現在の実名を参照できるようにする。
        if account.server_player_name != request.player_name:
            minecraft_name = (
                request.player_name.removeprefix(self._config.floodgate_username_prefix)
                if account.edition == "bedrock"
                else request.player_name
            )
            try:
                account = await asyncio.to_thread(
                    self._accounts.update_player_profile,
                    account.id,
                    minecraft_name=minecraft_name,
                    server_player_name=request.player_name,
                    player_uuid=request.player_uuid,
                )
            except ValueError as error:
                LOGGER.error(
                    "Could not update exchange player profile request=%s player_uuid=%s: %s",
                    request.request_id,
                    request.player_uuid,
                    error,
                )
                await self._release_material_buyback_if_needed(request)
                await self._send_minecraft_private_message(
                    request.player_name,
                    "現在のプレイヤー名を安全に確認できませんでした。管理者へご連絡ください。",
                )
                return

        user_id = account.discord_user_id
        if request.kind == "balance":
            shop = await self._level_bot_xp.fetch_xp_shop(guild_id, user_id)
            message = (
                "XP残高を取得できませんでした。少し待ってから再度お試しください。"
                if shop is None
                else (
                    f"現在XP: {shop.wallet.available_xp:,} XP / "
                    f"獲得 {shop.wallet.total_xp:,} XP / "
                    f"消費済み {shop.wallet.spent_xp:,} XP"
                )
            )
            await self._send_minecraft_private_message(request.player_name, message)
            return
        if request.kind == "xp":
            await self._handle_minecraft_xp_exchange_request(
                request,
                guild_id=guild_id,
                user_id=user_id,
            )
            return
        if request.kind == "resource":
            await self._handle_minecraft_resource_exchange_request(
                request,
                guild_id=guild_id,
                user_id=user_id,
            )
            return
        if request.kind == "material_buyback":
            await self._handle_minecraft_material_buyback_request(
                request,
                guild_id=guild_id,
                user_id=user_id,
                account_id=account.id,
            )
            return
        await self._handle_minecraft_emerald_exchange_request(request)

    async def _handle_market_listing(self, event: MinecraftMarketListingEvent) -> None:
        try:
            account = await asyncio.to_thread(self._accounts.get_by_player_uuid, event.seller_uuid)
        except ValueError as error:
            LOGGER.error(
                "Could not identify market seller listing=%d uuid=%s: %s",
                event.listing_id,
                event.seller_uuid,
                error,
            )
            account = None
        if account is None or account.status != "active" or account.discord_user_id is None:
            try:
                response = await self._execute_rcon(
                    market_transfer_command(
                        "return",
                        event.listing_id,
                        event.seller_uuid,
                        event.event_id,
                    )
                )
                transfer = parse_market_transfer_result(
                    response,
                    request_id=event.event_id,
                    listing_id=event.listing_id,
                )
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.error(
                    "Could not return unlinked market listing=%d: %s",
                    event.listing_id,
                    error,
                )
                raise RuntimeError("unlinked market escrow return is ambiguous") from error
            if transfer.status != "completed":
                raise RuntimeError(
                    "unlinked market escrow return is incomplete: "
                    f"status={transfer.status} listing_status={transfer.listing_status}"
                )
            await self._send_minecraft_private_message(
                event.seller_name,
                "マーケット利用にはDiscordアカウント連携が必要です。出品アイテムを返却しました。",
            )
            return
        listing, created = await asyncio.to_thread(
            self._market.add_listing,
            listing_id=event.listing_id,
            event_id=event.event_id,
            seller_account_id=account.id,
            seller_discord_user_id=account.discord_user_id,
            seller_uuid=event.seller_uuid,
            seller_name=event.seller_name,
            item_id=event.item_id,
            item_name=event.item_name,
            item_count=event.item_count,
            price_xp=event.price_xp,
            created_at=event.created_at,
        )
        if created or listing.discord_message_id is None:
            await self._post_market_listing(listing)
        else:
            await self._refresh_market_listing(listing.listing_id, move_panel=False)

    async def _handle_market_request(self, request: MinecraftMarketRequest) -> None:
        if self._fresh_game_request_time(request.requested_at) is None:
            await self._send_minecraft_private_message(
                request.player_name,
                "このマーケット操作は期限切れです。もう一度 /market を実行してください。",
            )
            return
        guild_id = self._settings.guild_id
        if guild_id is None or self._rcon is None:
            await self._send_minecraft_private_message(
                request.player_name,
                "マーケットは現在準備中です。少し待ってからお試しください。",
            )
            return
        try:
            account = await asyncio.to_thread(
                self._accounts.get_by_player_uuid, request.player_uuid
            )
        except ValueError as error:
            LOGGER.error(
                "Could not identify market account request=%s uuid=%s: %s",
                request.request_id,
                request.player_uuid,
                error,
            )
            account = None
        if account is None or account.status != "active" or account.discord_user_id is None:
            await self._send_minecraft_private_message(
                request.player_name,
                "マーケット利用にはDiscordアカウントとの連携が必要です。",
            )
            return
        if request.kind == "balance":
            wallet = await self._level_bot_xp.fetch_market_wallet(guild_id, account.discord_user_id)
            message = (
                "サーバーXP残高を取得できませんでした。少し待ってから再度お試しください。"
                if wallet is None
                else (
                    f"現在のサーバーXP: {wallet.available_xp:,} XP / "
                    f"獲得・売上のサーバーXP: {wallet.total_xp:,} XP / "
                    f"使用済み・予約中のサーバーXP: {wallet.spent_xp:,} XP"
                )
            )
        elif request.kind == "buy":
            message = await self._purchase_market(
                guild_id=guild_id,
                listing_id=request.listing_id,
                request_id=request.request_id,
                expected_price_xp=request.expected_price_xp,
                buyer=account,
            )
            if message is None:
                message = "購入結果を安全に確認できませんでした。同じ商品でもう一度お試しください。"
        else:
            message = await self._cancel_market(
                listing_id=request.listing_id,
                request_id=request.request_id,
                seller=account,
            )
            if message is None:
                message = "返却結果を確認できませんでした。同じ出品でもう一度お試しください。"
        await self._send_minecraft_private_message(request.player_name, message)

    async def show_market_balance(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        wallet = await self._level_bot_xp.fetch_market_wallet(
            interaction.guild_id, interaction.user.id
        )
        await interaction.followup.send(
            market_balance_text(wallet)
            if wallet is not None
            else "XP残高を取得できませんでした。少し待ってから再度お試しください。",
            ephemeral=True,
        )

    async def show_market_guide(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=market_guide_embed(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def show_market_purchase_confirmation(
        self, interaction: discord.Interaction, listing_id: int
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        listing = await asyncio.to_thread(self._market.get, listing_id)
        if listing is None or listing.status != "active":
            await interaction.followup.send("この商品は売り切れました。", ephemeral=True)
            return
        try:
            account, reason = await self._online_exchange_account(interaction.user.id)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not verify market buyer: %s", error)
            await interaction.followup.send(
                "Minecraftへの参加状況を確認できませんでした。", ephemeral=True
            )
            return
        if account is None:
            message = (
                "連携アカウントが複数オンラインです。受取先を1つだけオンラインにしてください。"
                if reason == "account_ambiguous"
                else "連携したMinecraftアカウントで参加してから購入してください。"
            )
            await interaction.followup.send(message, ephemeral=True)
            return
        if listing.seller_discord_user_id == interaction.user.id:
            await interaction.followup.send("自分の出品は購入できません。", ephemeral=True)
            return
        wallet = await self._level_bot_xp.fetch_market_wallet(
            interaction.guild_id, interaction.user.id
        )
        if wallet is None:
            await interaction.followup.send("XP残高を取得できませんでした。", ephemeral=True)
            return
        await interaction.followup.send(
            embed=market_purchase_confirmation_embed(listing, wallet),
            view=MarketPurchaseConfirmView(
                self,
                listing=listing,
                buyer_account_id=account.id,
                owner_id=interaction.user.id,
                wallet=wallet,
            ),
            ephemeral=True,
        )

    async def purchase_market_listing(
        self,
        interaction: discord.Interaction,
        *,
        listing_id: int,
        request_id: str,
        buyer_account_id: int,
    ) -> str | None:
        if interaction.guild_id is None:
            return "Discordサーバー内で利用してください。"
        buyer = await asyncio.to_thread(self._accounts.get, buyer_account_id)
        if (
            buyer is None
            or buyer.status != "active"
            or buyer.discord_user_id != interaction.user.id
            or buyer.player_uuid is None
        ):
            return "購入に使うMinecraftアカウントを確認できませんでした。"
        listing = await asyncio.to_thread(self._market.get, listing_id)
        if listing is None:
            return "この商品は見つかりません。"
        return await self._purchase_market(
            guild_id=interaction.guild_id,
            listing_id=listing_id,
            request_id=request_id,
            expected_price_xp=listing.price_xp,
            buyer=buyer,
        )

    async def cancel_market_listing(
        self, interaction: discord.Interaction, listing_id: int
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        listing = await asyncio.to_thread(self._market.get, listing_id)
        if listing is None or listing.seller_discord_user_id != interaction.user.id:
            await interaction.followup.send("取り消せるのは自分の出品だけです。", ephemeral=True)
            return
        seller = await asyncio.to_thread(self._accounts.get, listing.seller_account_id)
        if seller is None:
            await interaction.followup.send(
                "出品者アカウントを確認できませんでした。", ephemeral=True
            )
            return
        message = await self._cancel_market(
            listing_id=listing_id,
            request_id=str(uuid.uuid4()),
            seller=seller,
        )
        await interaction.followup.send(
            message or "返却結果を確認できませんでした。もう一度お試しください。",
            ephemeral=True,
        )

    async def _purchase_market(
        self,
        *,
        guild_id: int,
        listing_id: int,
        request_id: str,
        expected_price_xp: int,
        buyer: MinecraftAccount,
    ) -> str | None:
        if buyer.discord_user_id is None or buyer.player_uuid is None:
            return "購入アカウントを確認できませんでした。"
        async with self._market_lock:
            listing = await asyncio.to_thread(
                self._market.reserve_purchase,
                listing_id=listing_id,
                request_id=request_id,
                expected_price_xp=expected_price_xp,
                buyer_account_id=buyer.id,
                buyer_discord_user_id=buyer.discord_user_id,
            )
            if listing is None or listing.purchase_request_id is None:
                return "この商品は売り切れたか、価格が更新されました。"
            effective_request_id = listing.purchase_request_id
            purchase = await self._level_bot_xp.request_market_purchase(
                request_id=effective_request_id,
                guild_id=guild_id,
                listing_id=listing.listing_id,
                buyer_user_id=buyer.discord_user_id,
                seller_user_id=listing.seller_discord_user_id,
                buyer_account_id=buyer.id,
                seller_account_id=listing.seller_account_id,
                expected_cost_xp=listing.price_xp,
            )
            if purchase is None:
                return None
            if purchase.status != "reserved":
                await asyncio.to_thread(
                    self._market.set_status,
                    listing.listing_id,
                    effective_request_id,
                    "active",
                )
                await self._refresh_market_listing(listing.listing_id)
                return purchase.message
            try:
                response = await self._execute_rcon(
                    market_transfer_command(
                        "deliver",
                        listing.listing_id,
                        buyer.player_uuid,
                        effective_request_id,
                    )
                )
                transfer = parse_market_transfer_result(
                    response,
                    request_id=effective_request_id,
                    listing_id=listing.listing_id,
                )
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Market item delivery is ambiguous listing=%d request=%s: %s",
                    listing.listing_id,
                    effective_request_id,
                    error,
                )
                return None
            if transfer.status != "completed":
                if (
                    transfer.delivery_recorded
                    or transfer.status == "storage_error"
                    or transfer.listing_status == "delivering"
                ):
                    return None
                if transfer.status not in {
                    "unavailable",
                    "recipient_mismatch",
                    "player_offline",
                    "inventory_full",
                }:
                    return None
                cancelled = await self._level_bot_xp.update_market_purchase(
                    request_id=effective_request_id,
                    guild_id=guild_id,
                    action="cancel",
                )
                if not cancelled:
                    return None
                local_status = (
                    transfer.listing_status
                    if transfer.listing_status in {"sold", "cancelled"}
                    else "active"
                )
                await asyncio.to_thread(
                    self._market.set_status,
                    listing.listing_id,
                    effective_request_id,
                    local_status,
                )
                await self._refresh_market_listing(listing.listing_id)
                return {
                    "player_offline": (
                        "Minecraftへ参加してから購入してください。XPは消費していません。"
                    ),
                    "inventory_full": (
                        "インベントリを空けてから購入してください。XPは消費していません。"
                    ),
                }.get(transfer.status, "商品を受け取れませんでした。XPは消費していません。")
            completed = await self._level_bot_xp.update_market_purchase(
                request_id=effective_request_id,
                guild_id=guild_id,
                action="complete",
            )
            if not completed:
                return None
            await asyncio.to_thread(
                self._market.set_status,
                listing.listing_id,
                effective_request_id,
                "sold",
            )
            await self._refresh_market_listing(listing.listing_id)
            await self._deliver_market_purchase_notifications()
            return (
                f"購入完了: #{listing.listing_id} {listing.display_item_name} "
                f"x{listing.item_count} / "
                f"{listing.price_xp:,} サーバーXP。"
                f"残りのサーバーXPは {purchase.wallet_after.available_xp:,} XPです。"
            )

    async def _cancel_market(
        self, *, listing_id: int, request_id: str, seller: MinecraftAccount
    ) -> str | None:
        if seller.player_uuid is None:
            return "出品者のMinecraft UUIDを確認できませんでした。"
        async with self._market_lock:
            listing = await asyncio.to_thread(
                self._market.begin_cancel,
                listing_id=listing_id,
                seller_account_id=seller.id,
                request_id=request_id,
            )
            if listing is None or listing.purchase_request_id is None:
                return "その出品はすでに取引中か、取り消し済みです。"
            effective_request_id = listing.purchase_request_id
            try:
                response = await self._execute_rcon(
                    market_transfer_command(
                        "return",
                        listing.listing_id,
                        listing.seller_uuid,
                        effective_request_id,
                    )
                )
                transfer = parse_market_transfer_result(
                    response,
                    request_id=effective_request_id,
                    listing_id=listing.listing_id,
                )
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Market cancellation is ambiguous listing=%d request=%s: %s",
                    listing.listing_id,
                    effective_request_id,
                    error,
                )
                return None
            if transfer.status != "completed":
                if (
                    transfer.delivery_recorded
                    or transfer.status == "storage_error"
                    or transfer.listing_status == "delivering"
                ):
                    return None
                local_status = "cancelled" if transfer.listing_status == "cancelled" else "active"
                await asyncio.to_thread(
                    self._market.set_status,
                    listing.listing_id,
                    effective_request_id,
                    local_status,
                )
                await self._refresh_market_listing(listing.listing_id)
                return {
                    "player_offline": "Minecraftへ参加してから取り消してください。",
                    "inventory_full": "インベントリを空けてから取り消してください。",
                }.get(transfer.status, "アイテムを返却できませんでした。")
            await asyncio.to_thread(
                self._market.set_status,
                listing.listing_id,
                effective_request_id,
                "cancelled",
            )
            await self._refresh_market_listing(listing.listing_id)
            return (
                f"出品を取り消し、{listing.display_item_name} x{listing.item_count}を返却しました。"
            )

    async def _post_market_listing(
        self, listing: MarketListing, *, move_panel: bool = True
    ) -> None:
        channel_id = self._settings.market_channel_id
        if channel_id is None or listing.status != "active":
            return
        channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
        message = await channel.send(
            embed=market_listing_embed(listing),
            view=MarketListingView(self, listing.listing_id, active=True),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await asyncio.to_thread(self._market.set_discord_message, listing.listing_id, message.id)
        if move_panel:
            try:
                await self._refresh_market_panel(move_to_bottom=True)
            except (OSError, RuntimeError, discord.DiscordException) as error:
                LOGGER.warning("Could not move market panel after a new listing: %s", error)

    async def _refresh_market_listing(
        self,
        listing_id: int,
        *,
        move_panel: bool = True,
        edit_existing: bool = True,
    ) -> None:
        listing = await asyncio.to_thread(self._market.get, listing_id)
        channel_id = self._settings.market_channel_id
        if listing is None or channel_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            if listing.status == "sold":
                if listing.discord_message_id is None:
                    return
                try:
                    message = await channel.fetch_message(listing.discord_message_id)
                    await message.delete()
                except discord.NotFound:
                    pass
                await asyncio.to_thread(self._market.set_discord_message, listing.listing_id, None)
                return
            if listing.discord_message_id is None:
                await self._post_market_listing(listing, move_panel=move_panel)
                return
            message = await channel.fetch_message(listing.discord_message_id)
            if not edit_existing:
                return
            await message.edit(
                embed=market_listing_embed(listing),
                view=MarketListingView(
                    self,
                    listing.listing_id,
                    active=listing.status == "active",
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.NotFound:
            await self._post_market_listing(listing, move_panel=move_panel)

    async def _refresh_market_panel(self, *, move_to_bottom: bool = False) -> None:
        async with self._market_panel_lock:
            channel_id = self._settings.market_channel_id
            if channel_id is None:
                return
            channel = await self._resolve_and_validate_channel(
                channel_id,
                require_embeds=True,
                require_message_history=True,
            )
            message: discord.Message | None = None
            message_id = self._settings.market_panel_message_id
            if message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    message = None
            if message is not None and not move_to_bottom:
                await message.edit(
                    embed=market_panel_embed(),
                    view=MarketPanelView(self),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            if message is not None:
                with suppress(discord.NotFound):
                    await message.delete()
            message = await channel.send(
                embed=market_panel_embed(),
                view=MarketPanelView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self._save_settings(replace(self._settings, market_panel_message_id=message.id))

    async def _recover_market_transactions(self) -> None:
        guild_id = self._settings.guild_id
        if guild_id is None:
            return
        if self._settings.market_channel_id is not None:
            sold_listings = await asyncio.to_thread(self._market.list_sold_with_discord_message)
            for listing in sold_listings:
                try:
                    await self._refresh_market_listing(listing.listing_id, move_panel=False)
                except OSError, RuntimeError, discord.DiscordException:
                    LOGGER.exception(
                        "Could not remove sold market listing=%d",
                        listing.listing_id,
                    )
        for listing in await asyncio.to_thread(self._market.list_open):
            try:
                if listing.status == "active":
                    if listing.discord_message_id is None:
                        await self._post_market_listing(listing)
                    else:
                        await self._refresh_market_listing(
                            listing.listing_id,
                            move_panel=False,
                            edit_existing=False,
                        )
                    continue
                if listing.purchase_request_id is None:
                    continue
                if listing.status == "reserved" and listing.buyer_account_id is not None:
                    buyer = await asyncio.to_thread(self._accounts.get, listing.buyer_account_id)
                    if buyer is not None:
                        await self._purchase_market(
                            guild_id=guild_id,
                            listing_id=listing.listing_id,
                            request_id=listing.purchase_request_id,
                            expected_price_xp=listing.price_xp,
                            buyer=buyer,
                        )
                elif listing.status == "cancelling":
                    seller = await asyncio.to_thread(self._accounts.get, listing.seller_account_id)
                    if seller is not None:
                        await self._cancel_market(
                            listing_id=listing.listing_id,
                            request_id=listing.purchase_request_id,
                            seller=seller,
                        )
            except RuntimeError, discord.DiscordException:
                LOGGER.exception(
                    "Could not recover market listing=%d status=%s",
                    listing.listing_id,
                    listing.status,
                )
        await self._deliver_market_purchase_notifications()

    async def _deliver_market_purchase_notifications(self) -> None:
        async with self._market_notification_lock:
            listings = await asyncio.to_thread(self._market.list_pending_purchase_notifications)
            guild = self.get_guild(self._settings.guild_id or 0)
            server_name = guild.name if guild is not None else "サーバー"
            for listing in listings:
                if (
                    listing.purchase_request_id is None
                    or listing.buyer_account_id is None
                    or listing.buyer_discord_user_id is None
                ):
                    continue
                buyer = await asyncio.to_thread(
                    self._accounts.get,
                    listing.buyer_account_id,
                )
                if buyer is None:
                    LOGGER.warning(
                        "Could not notify market purchase for missing buyer account listing=%d",
                        listing.listing_id,
                    )
                    continue
                if not listing.minecraft_purchase_notified:
                    try:
                        await self._execute_checked_rcon(
                            market_purchase_tellraw_command(
                                server_name=server_name,
                                buyer_name=buyer.server_player_name,
                                seller_name=listing.seller_name,
                                item_name=listing.display_item_name,
                                item_count=listing.item_count,
                                price_xp=listing.price_xp,
                            )
                        )
                    except (OSError, RconError, RuntimeError, ValueError) as error:
                        LOGGER.warning(
                            "Could not announce market purchase in Minecraft listing=%d: %s",
                            listing.listing_id,
                            error,
                        )
                    else:
                        await asyncio.to_thread(
                            self._market.mark_purchase_notified,
                            listing.listing_id,
                            listing.purchase_request_id,
                            "minecraft",
                        )
                if not listing.discord_purchase_notified:
                    channel_id = self._settings.market_log_channel_id
                    if channel_id is None:
                        continue
                    try:
                        channel = await self._resolve_and_validate_channel(
                            channel_id,
                            require_embeds=True,
                        )
                        await channel.send(
                            embed=format_market_purchase(
                                server_name=server_name,
                                buyer_name=buyer.server_player_name,
                                buyer_discord_user_id=listing.buyer_discord_user_id,
                                seller_name=listing.seller_name,
                                seller_discord_user_id=listing.seller_discord_user_id,
                                item_name=listing.display_item_name,
                                item_count=listing.item_count,
                                price_xp=listing.price_xp,
                            ),
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except (RuntimeError, discord.DiscordException) as error:
                        LOGGER.warning(
                            "Could not announce market purchase in Discord listing=%d: %s",
                            listing.listing_id,
                            error,
                        )
                    else:
                        await asyncio.to_thread(
                            self._market.mark_purchase_notified,
                            listing.listing_id,
                            listing.purchase_request_id,
                            "discord",
                        )

    async def _handle_quest_state(self, event: MinecraftQuestStateEvent) -> None:
        owner = await self._quest_account(event.owner_uuid)
        worker = (
            await self._quest_account(event.worker_uuid) if event.worker_uuid is not None else None
        )
        quest, applied = await asyncio.to_thread(
            self._quests.apply_state,
            event,
            owner_account_id=owner.id if owner is not None else None,
            owner_discord_user_id=(owner.discord_user_id if owner is not None else None),
            worker_account_id=worker.id if worker is not None else None,
            worker_discord_user_id=(worker.discord_user_id if worker is not None else None),
        )
        if event.status == "open" and owner is None:
            await self._undo_unlinked_quest_state(
                event,
                action="invalidate",
                player_uuid=event.owner_uuid,
                player_name=event.owner_name,
                reason="クエスト利用にはDiscordアカウント連携が必要です。報酬は受取箱へ戻しました。",
            )
            return
        if event.status == "accepted" and event.worker_uuid is not None and worker is None:
            await self._undo_unlinked_quest_state(
                event,
                action="abandon",
                player_uuid=event.worker_uuid,
                player_name=event.worker_name or "player",
                reason="クエスト利用にはDiscordアカウント連携が必要です。受注を解除しました。",
            )
            return
        if applied or quest.status != "open" or quest.discord_message_id is None:
            await self._refresh_quest_listing(quest.quest_id)
        if quest.status in {"completed", "cancelled"}:
            await self._deliver_quest_logs()

    async def _quest_account(self, player_uuid: str | None) -> MinecraftAccount | None:
        if player_uuid is None:
            return None
        try:
            account = await asyncio.to_thread(self._accounts.get_by_player_uuid, player_uuid)
        except ValueError as error:
            LOGGER.error("Could not identify quest account uuid=%s: %s", player_uuid, error)
            return None
        if account is None or account.status != "active" or account.discord_user_id is None:
            return None
        return account

    async def _undo_unlinked_quest_state(
        self,
        event: MinecraftQuestStateEvent,
        *,
        action: str,
        player_uuid: str,
        player_name: str,
        reason: str,
    ) -> None:
        if self._rcon is None:
            raise RuntimeError("RCON is required to undo an unlinked quest state")
        request_id = str(
            uuid.uuid5(
                uuid.UUID(event.event_id),
                f"unlinked:{action}:{event.quest_id}:{player_uuid}",
            )
        )
        response = await self._execute_rcon(
            quest_action_command(
                action,
                event.quest_id,
                player_uuid,
                request_id,
            )
        )
        result = parse_quest_action_result(
            response,
            request_id=request_id,
            quest_id=event.quest_id,
        )
        if result.status != "completed":
            already_reconciled = (
                action == "invalidate" and result.quest_status in {"completed", "cancelled"}
            ) or (action == "abandon" and result.quest_status in {"open", "completed", "cancelled"})
            if already_reconciled:
                return
            raise RuntimeError(
                f"could not undo unlinked quest: {result.status}/{result.quest_status}"
            )
        expected_status = "cancelled" if action == "invalidate" else "open"
        if result.quest_status == expected_status:
            await self._send_minecraft_private_message(player_name, reason)

    async def accept_quest(self, interaction: discord.Interaction, quest_id: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        quest = await asyncio.to_thread(self._quests.get, quest_id)
        if quest is None or quest.status != "open":
            await interaction.followup.send("このクエストは募集を終了しました。", ephemeral=True)
            return
        if quest.owner_discord_user_id == interaction.user.id:
            await interaction.followup.send("自分の依頼は受注できません。", ephemeral=True)
            return
        try:
            account, reason = await self._online_exchange_account(interaction.user.id)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not verify quest worker: %s", error)
            await interaction.followup.send(
                "Minecraftへの参加状況を確認できませんでした。", ephemeral=True
            )
            return
        if account is None or account.player_uuid is None:
            message = (
                "連携アカウントが複数オンラインです。受注先を1つだけオンラインにしてください。"
                if reason == "account_ambiguous"
                else "連携したMinecraftアカウントで参加してから受注してください。"
            )
            await interaction.followup.send(message, ephemeral=True)
            return
        result = await self._run_quest_action(
            "accept",
            quest,
            account.player_uuid,
            player_name=account.server_player_name,
        )
        if result is None:
            await interaction.followup.send(
                "受注結果を確認できませんでした。ゲーム内の `/quest mine` を確認してください。",
                ephemeral=True,
            )
            return
        if result.status == "completed":
            await self._delete_quest_card(quest)
            await interaction.followup.send(
                f"クエスト #{quest.quest_id} を受注しました。"
                "納品はMinecraftで依頼品を持ち `/quest submit "
                f"{quest.quest_id}` を実行してください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(self._quest_action_error(result.status), ephemeral=True)

    async def show_quest_action_confirmation(
        self,
        interaction: discord.Interaction,
        quest_id: int,
        action: str,
        *,
        return_page: int | None = None,
    ) -> None:
        if action not in {"accept", "cancel", "submit", "abandon"}:
            await interaction.response.send_message("不明な操作です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        quest = await asyncio.to_thread(self._quests.get, quest_id)
        invalid_message: str | None = None
        if quest is None:
            invalid_message = "このクエストは見つかりません。"
        elif action == "accept" and (
            quest.status != "open" or quest.owner_discord_user_id == interaction.user.id
        ):
            invalid_message = (
                "自分の依頼は受注できません。"
                if quest.owner_discord_user_id == interaction.user.id
                else "このクエストは募集を終了しました。"
            )
        elif action == "cancel" and (
            quest.status != "open" or quest.owner_discord_user_id != interaction.user.id
        ):
            invalid_message = "取り消せるのは募集中の自分の依頼だけです。"
        elif action in {"submit", "abandon"} and (
            quest.status != "accepted" or quest.worker_discord_user_id != interaction.user.id
        ):
            invalid_message = "このクエストの担当者ではありません。"
        if invalid_message is not None or quest is None:
            await interaction.followup.send(invalid_message, ephemeral=True)
            return
        await interaction.followup.send(
            embed=quest_action_confirmation_embed(quest, action),
            view=QuestActionConfirmationView(
                self,
                quest,
                owner_id=interaction.user.id,
                action=action,
                return_page=return_page,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def cancel_quest(self, interaction: discord.Interaction, quest_id: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        quest = await asyncio.to_thread(self._quests.get, quest_id)
        if (
            quest is None
            or quest.status != "open"
            or quest.owner_discord_user_id != interaction.user.id
        ):
            await interaction.followup.send(
                "取り消せるのは募集中の自分の依頼だけです。", ephemeral=True
            )
            return
        result = await self._run_quest_action("cancel", quest, quest.owner_uuid)
        if result is not None and result.status == "completed":
            await self._delete_quest_card(quest)
            await interaction.followup.send(
                "依頼を取り消しました。報酬はMinecraftの `/quest claim` で受け取れます。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            self._quest_action_error(result.status if result is not None else "unknown"),
            ephemeral=True,
        )

    async def submit_quest(self, interaction: discord.Interaction, quest_id: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        quest = await asyncio.to_thread(self._quests.get, quest_id)
        if (
            quest is None
            or quest.status != "accepted"
            or quest.worker_discord_user_id != interaction.user.id
        ):
            await interaction.followup.send("このクエストの担当者ではありません。", ephemeral=True)
            return
        try:
            account, reason = await self._online_exchange_account(interaction.user.id)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not verify quest submitter: %s", error)
            await interaction.followup.send(
                "Minecraftへの参加状況を確認できませんでした。", ephemeral=True
            )
            return
        if account is None or account.player_uuid is None or account.id != quest.worker_account_id:
            message = (
                "連携アカウントが複数オンラインです。受注したアカウントだけをオンラインにしてください。"
                if reason == "account_ambiguous"
                else "受注したMinecraftアカウントで参加してから納品してください。"
            )
            await interaction.followup.send(message, ephemeral=True)
            return
        result = await self._run_quest_action("submit", quest, account.player_uuid)
        if result is not None and result.status == "completed":
            await interaction.followup.send(
                "納品が完了しました。報酬はMinecraftの `/quest claim` で受け取れます。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            self._quest_action_error(result.status if result is not None else "unknown"),
            ephemeral=True,
        )

    async def abandon_quest(self, interaction: discord.Interaction, quest_id: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        quest = await asyncio.to_thread(self._quests.get, quest_id)
        if (
            quest is None
            or quest.status != "accepted"
            or quest.worker_discord_user_id != interaction.user.id
            or quest.worker_uuid is None
        ):
            await interaction.followup.send("このクエストの担当者ではありません。", ephemeral=True)
            return
        result = await self._run_quest_action("abandon", quest, quest.worker_uuid)
        if result is not None and result.status == "completed":
            await interaction.followup.send(
                "クエストを辞退しました。依頼は掲示板で再募集されます。", ephemeral=True
            )
            return
        await interaction.followup.send(
            self._quest_action_error(result.status if result is not None else "unknown"),
            ephemeral=True,
        )

    async def _run_quest_action(
        self,
        action: str,
        quest: Quest,
        player_uuid: str,
        *,
        player_name: str | None = None,
    ):
        request_id = str(uuid.uuid4())
        try:
            async with self._quest_lock:
                response = await self._execute_rcon(
                    quest_action_command(
                        action,
                        quest.quest_id,
                        player_uuid,
                        request_id,
                        player_name=player_name,
                    )
                )
                return parse_quest_action_result(
                    response,
                    request_id=request_id,
                    quest_id=quest.quest_id,
                )
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning(
                "Quest action is ambiguous quest=%d action=%s request=%s: %s",
                quest.quest_id,
                action,
                request_id,
                error,
            )
            return None

    @staticmethod
    def _quest_action_error(status: str) -> str:
        return {
            "unavailable": "そのクエストは募集を終了しました。",
            "own_quest": "自分の依頼は受注できません。",
            "not_assignee": "そのクエストの担当者ではありません。",
            "not_cancellable": "受注後の依頼は取り消せません。",
            "expired": "納品期限を過ぎています。",
            "item_mismatch": "依頼品を必要数、Minecraftのメインハンドにまとめて持ってください。",
            "player_offline": "Minecraftに参加してから操作してください。",
            "pending_recovered": "前回の納品処理を復旧しました。もう一度操作してください。",
            "storage_error": "保存に失敗しました。少し待ってからもう一度お試しください。",
        }.get(
            status,
            "操作結果を確認できませんでした。Minecraftの `/quest mine` を確認してください。",
        )

    async def show_quest_guide(
        self,
        interaction: discord.Interaction,
        *,
        update_message: bool = False,
    ) -> None:
        options = {
            "content": None,
            "embed": quest_guide_embed(),
            "view": QuestBackView(self, interaction.user.id),
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if update_message:
            await interaction.response.edit_message(**options)
        else:
            await interaction.response.send_message(**options, ephemeral=True)

    async def show_quest_claim_guide(
        self,
        interaction: discord.Interaction,
        *,
        update_message: bool = False,
    ) -> None:
        options = {
            "content": (
                "報酬・返却品・納品物はMinecraftの永続受取箱に入ります。"
                "インベントリに空きを作り、Minecraftで `/quest claim` を実行してください。"
            ),
            "embed": None,
            "view": QuestBackView(self, interaction.user.id),
        }
        if update_message:
            await interaction.response.edit_message(**options)
        else:
            await interaction.response.send_message(**options, ephemeral=True)

    async def show_my_quests(
        self,
        interaction: discord.Interaction,
        *,
        page: int = 0,
        update_message: bool = False,
    ) -> None:
        if update_message:
            await interaction.response.defer()
        else:
            # For component interactions, ephemeral only applies with thinking=True.
            # Without it, Discord updates the public panel that was clicked.
            await interaction.response.defer(ephemeral=True, thinking=True)
        quests = await asyncio.to_thread(
            self._quests.list_active_for_discord_user, interaction.user.id
        )
        if not quests:
            await interaction.edit_original_response(
                content=(
                    "進行中の依頼・受注はありません。"
                    "受取品はMinecraftの `/quest claim` で確認できます。"
                ),
                embed=None,
                view=QuestBackView(self, interaction.user.id),
            )
            return
        selected = min(max(0, page), len(quests) - 1)
        quest = quests[selected]
        await interaction.edit_original_response(
            content=None,
            embed=quest_mine_embed(quest, interaction.user.id, page=selected, total=len(quests)),
            view=QuestMineView(
                self,
                quest,
                interaction.user.id,
                page=selected,
                total=len(quests),
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _post_quest_listing(self, quest: Quest, *, move_panel: bool = True) -> None:
        channel_id = self._settings.quest_channel_id
        if channel_id is None or quest.status != "open" or quest.owner_discord_user_id is None:
            return
        channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
        message = await channel.send(
            embed=quest_listing_embed(quest),
            view=QuestListingView(self, quest.quest_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await asyncio.to_thread(self._quests.set_discord_message, quest.quest_id, message.id)
        if move_panel:
            await self._refresh_quest_panel(move_to_bottom=True)

    async def _refresh_quest_listing(
        self,
        quest_id: int,
        *,
        move_panel: bool = True,
        edit_existing: bool = True,
    ) -> None:
        quest = await asyncio.to_thread(self._quests.get, quest_id)
        if quest is None:
            return
        if quest.status != "open":
            await self._delete_quest_card(quest)
            return
        channel_id = self._settings.quest_channel_id
        if channel_id is None:
            return
        if quest.discord_message_id is None:
            await self._post_quest_listing(quest, move_panel=move_panel)
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            message = await channel.fetch_message(quest.discord_message_id)
            if not edit_existing and quest_listing_has_current_controls(message, quest.quest_id):
                return
            await message.edit(
                embed=quest_listing_embed(quest),
                view=QuestListingView(self, quest.quest_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.NotFound:
            await asyncio.to_thread(self._quests.set_discord_message, quest.quest_id, None)
            await self._post_quest_listing(quest, move_panel=move_panel)

    async def _delete_quest_card(self, quest: Quest, *, channel_id: int | None = None) -> None:
        if quest.discord_message_id is None:
            return
        target_channel = channel_id or self._settings.quest_channel_id
        if target_channel is not None:
            try:
                channel = await self._resolve_and_validate_channel(target_channel)
                message = await channel.fetch_message(quest.discord_message_id)
                await message.delete()
            except discord.NotFound:
                pass
        await asyncio.to_thread(self._quests.set_discord_message, quest.quest_id, None)

    async def _refresh_quest_panel(self, *, move_to_bottom: bool = False) -> None:
        async with self._quest_panel_lock:
            channel_id = self._settings.quest_channel_id
            if channel_id is None:
                return
            channel = await self._resolve_and_validate_channel(
                channel_id,
                require_embeds=True,
                require_message_history=True,
            )
            message: discord.Message | None = None
            message_id = self._settings.quest_panel_message_id
            if message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    message = None
            if message is not None and not move_to_bottom:
                await message.edit(
                    embed=quest_panel_embed(),
                    view=QuestPanelView(self),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            if message is not None:
                with suppress(discord.NotFound):
                    await message.delete()
            message = await channel.send(
                embed=quest_panel_embed(),
                view=QuestPanelView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self._save_settings(replace(self._settings, quest_panel_message_id=message.id))

    async def _recover_quests(self) -> None:
        for quest in await asyncio.to_thread(self._quests.list_nonopen_with_discord_message):
            try:
                await self._delete_quest_card(quest)
            except OSError, RuntimeError, discord.DiscordException:
                LOGGER.exception("Could not remove inactive quest card quest=%d", quest.quest_id)
        for quest in await asyncio.to_thread(self._quests.list_open):
            try:
                await self._refresh_quest_listing(
                    quest.quest_id,
                    move_panel=False,
                    edit_existing=False,
                )
            except OSError, RuntimeError, discord.DiscordException:
                LOGGER.exception("Could not restore open quest card quest=%d", quest.quest_id)
        await self._deliver_quest_logs()

    async def _deliver_quest_logs(self) -> None:
        async with self._quest_notification_lock:
            channel_id = self._settings.quest_log_channel_id
            if channel_id is None:
                return
            channel = await self._resolve_and_validate_channel(
                channel_id,
                require_embeds=True,
                require_message_history=True,
            )
            quests = await asyncio.to_thread(self._quests.list_terminal_unnotified)
            attempted = [quest for quest in quests if quest.discord_log_delivery_attempted]
            existing = await self._existing_quest_log_transitions(channel, attempted)
            for quest in quests:
                if quest.last_transition_id in existing:
                    await asyncio.to_thread(
                        self._quests.mark_discord_log_notified,
                        quest.quest_id,
                        quest.last_transition_id,
                    )
                    continue
                if not quest.discord_log_delivery_attempted:
                    await asyncio.to_thread(
                        self._quests.mark_discord_log_delivery_attempted,
                        quest.quest_id,
                        quest.last_transition_id,
                    )
                await channel.send(
                    embed=quest_log_embed(quest),
                    nonce=quest_log_nonce(quest.last_transition_id),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await asyncio.to_thread(
                    self._quests.mark_discord_log_notified,
                    quest.quest_id,
                    quest.last_transition_id,
                )

    async def _existing_quest_log_transitions(
        self,
        channel: discord.TextChannel,
        quests: list[Quest],
    ) -> set[str]:
        if not quests:
            return set()
        pending = {quest.last_transition_id for quest in quests}
        nonces = {quest_log_nonce(transition_id): transition_id for transition_id in pending}
        earliest = min(datetime.fromisoformat(quest.published_at) for quest in quests) - timedelta(
            seconds=1
        )
        found: set[str] = set()
        async for message in channel.history(limit=None, after=earliest, oldest_first=False):
            if self.user is not None and message.author.id != self.user.id:
                continue
            if message.nonce is not None:
                try:
                    transition_id = nonces.get(int(message.nonce))
                except TypeError, ValueError:
                    transition_id = None
                if transition_id is not None:
                    found.add(transition_id)
            for embed in message.embeds:
                footer = embed.footer.text or ""
                for transition_id in pending - found:
                    if f"記録ID: {transition_id}" in footer:
                        found.add(transition_id)
            if found == pending:
                break
        return found

    def _ensure_market_recovery_started(self) -> None:
        if self._market_recovery_task is not None and not self._market_recovery_task.done():
            return
        self._market_recovery_task = asyncio.create_task(
            self._market_recovery_loop(), name="minecraft-market-recovery"
        )

    async def _market_recovery_loop(self) -> None:
        while not self.is_closed():
            await asyncio.sleep(_MARKET_RECOVERY_INTERVAL_SECONDS)
            try:
                await self._recover_market_transactions()
                await self._recover_quests()
            except Exception:
                LOGGER.exception("Could not run periodic Minecraft market/quest recovery")

    async def _retry_integration_log_operation(
        self,
        *,
        description: str,
        operation: Callable[[], Awaitable[None]],
    ) -> bool:
        retry_delay = 1
        while not self.is_closed():
            try:
                await operation()
            except Exception:
                self._delivery_healthy = False
                LOGGER.exception(
                    "Minecraft integration %s failed; retrying in %ds",
                    description,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
                continue
            self._delivery_healthy = True
            return True
        return False

    async def _handle_minecraft_xp_exchange_request(
        self,
        request: MinecraftExchangeRequest,
        *,
        guild_id: int,
        user_id: int,
    ) -> None:
        shop = await self._level_bot_xp.fetch_xp_shop(guild_id, user_id)
        pack = (
            next(
                (
                    candidate
                    for candidate in shop.packs
                    if candidate.cost_xp == request.expected_cost_xp
                    and candidate.reward_xp == request.expected_reward
                ),
                None,
            )
            if shop is not None
            else None
        )
        if pack is None:
            await self._send_minecraft_private_message(
                request.player_name,
                "XP交換の価格が更新されたか、交換所を取得できませんでした。"
                "もう一度 /exchange を開いてください。",
            )
            return
        result = await self._level_bot_xp.request_xp_exchange(
            guild_id,
            user_id,
            request.request_id,
            pack.cost_xp,
            pack.reward_xp,
        )
        await self._send_minecraft_private_message(
            request.player_name,
            result.message
            if result is not None
            else "XP交換結果を確認できませんでした。少し待ってから再度お試しください。",
        )

    async def _handle_minecraft_resource_exchange_request(
        self,
        request: MinecraftExchangeRequest,
        *,
        guild_id: int,
        user_id: int,
    ) -> None:
        shop = await self._level_bot_xp.fetch_resource_shop(guild_id, user_id)
        pack = (
            next(
                (
                    candidate
                    for candidate in shop.packs
                    if candidate.item_id == request.target
                    and candidate.item_count == request.amount
                    and candidate.cost_xp == request.expected_cost_xp
                ),
                None,
            )
            if shop is not None
            else None
        )
        if pack is None:
            await self._send_minecraft_private_message(
                request.player_name,
                "資源交換の価格が更新されたか、交換所を取得できませんでした。"
                "もう一度 /exchange を開いてください。",
            )
            return
        result = await self._level_bot_xp.request_resource_exchange(
            guild_id,
            user_id,
            request.request_id,
            pack.item_id,
            pack.item_count,
            pack.cost_xp,
        )
        await self._send_minecraft_private_message(
            request.player_name,
            result.message
            if result is not None
            else "資源交換結果を確認できませんでした。少し待ってから再度お試しください。",
        )

    async def _handle_minecraft_emerald_exchange_request(
        self,
        request: MinecraftExchangeRequest,
    ) -> None:
        try:
            response = await self._execute_rcon(
                emerald_diamond_exchange_command(
                    request.player_uuid,
                    request.amount,
                    request.request_id,
                )
            )
            result = parse_emerald_diamond_exchange_result(
                response,
                expected_request_id=request.request_id,
                expected_emerald_count=request.amount,
            )
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning(
                "Could not complete game-command emerald exchange request=%s: %s",
                request.request_id,
                error,
            )
            await self._send_minecraft_private_message(
                request.player_name,
                "エメラルド交換結果を確認できませんでした。アイテム欄を確認し、"
                "不明な場合は管理者へご連絡ください。",
            )
            return
        messages = {
            "completed": (
                "この交換は完了済みです。"
                if result.duplicate
                else (
                    f"交換完了: エメラルド x{result.emerald_count} → "
                    f"ダイヤモンド x{result.diamond_count}"
                )
            ),
            "insufficient_emeralds": "手持ちのエメラルドが不足しています。",
            "inventory_full": "ダイヤモンドを受け取る空きがありません。",
            "player_offline": "プレイヤーのオンライン状態を確認できませんでした。",
        }
        await self._send_minecraft_private_message(
            request.player_name,
            messages[result.status],
        )

    async def _handle_minecraft_material_buyback_request(
        self,
        request: MinecraftExchangeRequest,
        *,
        guild_id: int,
        user_id: int,
        account_id: int,
    ) -> None:
        reservation = await self._level_bot_xp.request_material_buyback(
            request_id=request.request_id,
            guild_id=guild_id,
            user_id=user_id,
            account_id=account_id,
            item_id=request.target,
            item_count=request.amount,
            expected_reward_xp=request.expected_reward,
        )
        if reservation is None:
            raise RuntimeError("material buyback reservation could not be confirmed")
        if reservation.status in {"daily_limit", "unavailable", "conflict"}:
            await self._release_material_buyback_request(request)
            await self._send_minecraft_private_message(
                request.player_name,
                reservation.message,
            )
            return
        if (
            reservation.request_id != request.request_id
            or reservation.item_id != request.target
            or reservation.item_count != request.amount
            or reservation.reward_xp != request.expected_reward
        ):
            raise RuntimeError("level-bot returned an unbound material buyback reservation")
        if reservation.status == "completed":
            await self._release_material_buyback_request(request)
            message = await self._material_buyback_success_message(
                guild_id=guild_id,
                user_id=user_id,
                item_name=reservation.item_name,
                item_count=reservation.item_count,
                reward_xp=reservation.reward_xp,
                daily_reserved_xp=reservation.daily_reserved_xp,
                daily_limit_xp=reservation.daily_limit_xp,
                duplicate=True,
            )
            await self._send_minecraft_private_message(
                request.player_name,
                message,
            )
            return

        response = await self._execute_rcon(
            material_buyback_command(
                request.player_uuid,
                request.target,
                request.amount,
                request.request_id,
            )
        )
        result = parse_material_buyback_result(
            response,
            expected_request_id=request.request_id,
            expected_item_id=request.target,
            expected_item_count=request.amount,
        )
        if result.status == "completed":
            if not await self._level_bot_xp.update_material_buyback(
                request_id=request.request_id,
                guild_id=guild_id,
                user_id=user_id,
                action="complete",
            ):
                raise RuntimeError("material buyback XP completion could not be confirmed")
            await self._release_material_buyback_request(request)
            message = await self._material_buyback_success_message(
                guild_id=guild_id,
                user_id=user_id,
                item_name=reservation.item_name,
                item_count=request.amount,
                reward_xp=reservation.reward_xp,
                daily_reserved_xp=reservation.daily_reserved_xp,
                daily_limit_xp=reservation.daily_limit_xp,
                duplicate=result.duplicate,
            )
            await self._send_minecraft_private_message(
                request.player_name,
                message,
            )
            return

        if not await self._level_bot_xp.update_material_buyback(
            request_id=request.request_id,
            guild_id=guild_id,
            user_id=user_id,
            action="cancel",
        ):
            raise RuntimeError("material buyback cancellation could not be confirmed")
        await self._release_material_buyback_request(request)
        messages = {
            "insufficient_items": (
                f"通常の{reservation.item_name}が不足しています。"
                "名前や特殊データのない資材を64個単位で入れてください。"
            ),
            "player_offline": "プレイヤーのオンライン状態を確認できませんでした。",
            "storage_error": (
                "資材の保存処理を完了できませんでした。アイテム数を確認し、"
                "不明な場合は管理者へご連絡ください。"
            ),
        }
        await self._send_minecraft_private_message(
            request.player_name,
            messages[result.status],
        )

    async def _release_material_buyback_if_needed(self, request: MinecraftExchangeRequest) -> None:
        if request.kind == "material_buyback":
            await self._release_material_buyback_request(request)

    async def _release_material_buyback_request(self, request: MinecraftExchangeRequest) -> None:
        response = await self._execute_rcon(
            material_buyback_release_command(request.player_uuid, request.request_id)
        )
        parse_material_buyback_release_result(
            response,
            expected_player_uuid=request.player_uuid,
            expected_request_id=request.request_id,
        )

    async def _material_buyback_success_message(
        self,
        *,
        guild_id: int,
        user_id: int,
        item_name: str | None,
        item_count: int,
        reward_xp: int,
        daily_reserved_xp: int,
        daily_limit_xp: int,
        duplicate: bool,
    ) -> str:
        if item_name is None:
            raise RuntimeError("material buyback completion has no item name")
        status = "買取完了（処理済み）" if duplicate else "買取完了"  # noqa: RUF001
        message = f"{status}: 通常の{item_name} x{item_count:,} → +{reward_xp:,} サーバーXP"
        shop = await self._level_bot_xp.fetch_xp_shop(guild_id, user_id)
        if shop is not None:
            message += f" / 現在 {shop.wallet.available_xp:,} サーバーXP"
        else:
            message += " / 現在の残高は /exchange balance で確認できます"
        remaining = max(0, daily_limit_xp - daily_reserved_xp)
        return (
            message
            + f" / 本日の残り買取枠 {remaining:,} サーバーXP。"
            + "エメラルドは /exchange の「資源へ交換」から交換できます。"
        )

    @staticmethod
    def _fresh_game_request_time(value: str) -> datetime | None:
        requested_at = datetime.fromisoformat(value)
        if requested_at.utcoffset() is None:
            return None
        now = datetime.now(UTC)
        if (
            requested_at < now - _GAME_REQUEST_MAX_AGE
            or requested_at > now + _GAME_REQUEST_CLOCK_SKEW
        ):
            return None
        return requested_at

    async def _send_minecraft_private_message(
        self,
        player_name: str,
        message: str,
    ) -> None:
        try:
            await self._execute_checked_rcon(private_tellraw_command(player_name, message))
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning(
                "Could not send private Minecraft response to %s: %s",
                player_name,
                error,
            )

    @staticmethod
    def _item_gacha_draw_matches_catalog(draw: MinecraftItemGachaDraw) -> bool:
        try:
            reward = get_item_gacha_reward(draw.reward_key)
        except ValueError:
            return False
        return (
            draw.tier == reward.tier
            and draw.item_spec == reward.item_spec
            and draw.item_name == reward.item_name
            and draw.item_count == reward.item_count
            and draw.draw_kind in {"normal", "premium"}
            and draw.draw_category in {"all", "resources", "adventure", "equipment"}
            and (
                draw.draw_category == "all"
                or draw.draw_category in item_gacha_reward_categories(reward)
            )
            and draw.cost_xp
            == item_gacha_cost_xp("premium" if draw.draw_kind == "premium" else "normal")
            and not (draw.draw_kind == "premium" and draw.tier == "N")
        )

    @staticmethod
    def _item_gacha_received_text(draw: MinecraftItemGachaDraw, *, already: bool) -> str:
        prefix = "この抽選は受取済みです" if already else "受け取りました"
        return (
            f"{prefix}: **【{item_gacha_tier_label(draw.tier)}】"
            f"{discord.utils.escape_markdown(draw.item_name)} x{draw.item_count}**"
            f" / {item_gacha_category_label(cast(ItemGachaCategory, draw.draw_category))}"
            f" / {item_gacha_kind_label('premium' if draw.draw_kind == 'premium' else 'normal')}"
            f"・サーバーXP **{draw.cost_xp:,}**消費"
            f" (本日 {draw.draw_number}/{ITEM_GACHA_DAILY_LIMIT}回)"
        )

    async def show_minecraft_resource_shop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        shop = await self._level_bot_xp.fetch_resource_shop(
            interaction.guild_id, interaction.user.id
        )
        if shop is None:
            await interaction.followup.send(
                "資源交換所を取得できませんでした。少し待ってから再度お試しください。",
                ephemeral=True,
            )
            return
        if interaction.message is not None:
            try:
                await interaction.message.edit(
                    embed=minecraft_resource_shop_embed(shop.packs),
                    view=MinecraftResourceShopPanelView(self),
                )
            except discord.DiscordException as error:
                LOGGER.warning(
                    "Could not refresh Minecraft resource shop panel on use: %s",
                    error,
                )
        await interaction.followup.send(
            (
                f"交換可能XP: **{shop.wallet.available_xp:,} XP**\n"
                "交換内容を選んでください。"
                "Minecraftサーバーへの参加中のみ交換できます。"
            ),
            view=MinecraftResourcePackSelectView(self, owner_id=interaction.user.id, shop=shop),
            ephemeral=True,
        )

    async def show_minecraft_resource_balance(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        shop = await self._level_bot_xp.fetch_resource_shop(
            interaction.guild_id, interaction.user.id
        )
        if shop is None:
            await interaction.followup.send(
                "XP残高を取得できませんでした。少し待ってから再度お試しください。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(wallet_text(shop.wallet), ephemeral=True)

    async def show_emerald_diamond_exchange(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Discordサーバー内で利用してください。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            account, reason = await self._online_exchange_account(interaction.user.id)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not verify player for emerald exchange: %s", error)
            await interaction.followup.send(
                "Minecraftサーバーへの参加状況を確認できませんでした。"
                "少し待ってから再度お試しください。",
                ephemeral=True,
            )
            return
        if account is None:
            message = (
                "連携したMinecraftアカウントが複数同時にオンラインです。"
                "交換に使う1アカウントだけで参加してから再度お試しください。"
                if reason == "account_ambiguous"
                else "連携したMinecraftアカウントでサーバーに参加してからご利用ください。"
            )
            await interaction.followup.send(message, ephemeral=True)
            return
        await interaction.followup.send(
            "手持ちのエメラルドと交換する数量を選んでください。\n"
            "交換完了はMinecraft内チャットとDiscordログへ通知されます。",
            view=EmeraldDiamondPackSelectView(self, owner_id=interaction.user.id),
            ephemeral=True,
        )

    async def confirm_minecraft_resource_exchange(
        self,
        interaction: discord.Interaction,
        *,
        request_id: str,
        item_id: str,
        item_count: int,
        expected_cost_xp: int,
    ) -> MinecraftResourceExchangeRequest | None:
        if interaction.guild_id is None:
            return None
        return await self._level_bot_xp.request_resource_exchange(
            interaction.guild_id,
            interaction.user.id,
            request_id,
            item_id,
            item_count,
            expected_cost_xp,
        )

    async def confirm_emerald_diamond_exchange(
        self,
        interaction: discord.Interaction,
        *,
        request_id: str,
        emerald_count: int,
    ) -> EmeraldDiamondExchangeResult | None:
        try:
            account, reason = await self._online_exchange_account(interaction.user.id)
            if account is None:
                return EmeraldDiamondExchangeResult(
                    request_id=str(uuid.UUID(request_id)),
                    status=reason or "player_offline",
                    emerald_count=emerald_count,
                    diamond_count=emerald_count // 32,
                    duplicate=False,
                )
            if account.player_uuid is None:
                raise ValueError("linked Minecraft account has no UUID")
            command = emerald_diamond_exchange_command(
                account.player_uuid,
                emerald_count,
                request_id,
            )
            response = await self._execute_rcon(command)
            return parse_emerald_diamond_exchange_result(
                response,
                expected_request_id=request_id,
                expected_emerald_count=emerald_count,
            )
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not complete emerald-diamond exchange: %s", error)
            return None

    async def _online_exchange_account(
        self, discord_user_id: int
    ) -> tuple[MinecraftAccount | None, str | None]:
        online_names = await self._online_players()
        linked = await self._linked_accounts_by_online_name(online_names)
        matches = {
            account.id: account
            for account in linked.values()
            if account.discord_user_id == discord_user_id
            and account.status == "active"
            and account.player_uuid is not None
        }
        if not matches:
            return None, "player_offline"
        if len(matches) > 1:
            return None, "account_ambiguous"
        return next(iter(matches.values())), None

    async def confirm_registration(
        self,
        interaction: discord.Interaction,
        *,
        edition: str,
        minecraft_name: str,
        target: discord.Member,
        source: str,
    ) -> None:
        try:
            normalized_name, _ = self._normalize_player_name(edition, minecraft_name)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        edition_label = "Java版" if edition == "java" else "Bedrock版"
        name_label = "Java版のプレイヤー名" if edition == "java" else "Xboxゲーマータグ"
        target_line = f"\nDiscordユーザー: {target.mention}" if source == "admin" else ""
        await interaction.response.send_message(
            f"次の内容で登録します。\n\n"
            f"エディション: **{edition_label}**\n"
            f"{name_label}: **{discord.utils.escape_markdown(normalized_name)}**"
            f"{target_line}",
            view=ConfirmRegistrationView(
                self,
                owner_id=interaction.user.id,
                target=target,
                edition=edition,
                minecraft_name=normalized_name,
                source=source,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def register_account(
        self,
        interaction: discord.Interaction,
        *,
        edition: str,
        minecraft_name: str,
        target: discord.Member,
        source: str,
    ) -> None:
        try:
            normalized_name, server_name = self._normalize_player_name(edition, minecraft_name)
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        try:
            server_name, player_uuid = await self._resolve_player_profile(
                edition=edition,
                minecraft_name=normalized_name,
                server_player_name=server_name,
            )
        except (OSError, RuntimeError, ValueError) as error:
            await interaction.followup.send(
                f"Minecraftアカウントを確認できないため登録しませんでした: {error}",
                ephemeral=True,
            )
            return
        normalized_name = (
            server_name.removeprefix(self._config.floodgate_username_prefix)
            if edition == "bedrock"
            else server_name
        )
        automatic = self._settings.approval_mode == "automatic" or source == "admin"
        status = "pending_add" if automatic else "pending_approval"
        try:
            account = await asyncio.to_thread(
                self._accounts.create_registration,
                edition=edition,
                minecraft_name=normalized_name,
                server_player_name=server_name,
                discord_user_id=target.id,
                discord_username=target.display_name,
                source=source,
                status=status,
                created_by=interaction.user.id,
                player_uuid=player_uuid,
            )
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        if not automatic:
            try:
                await self._post_approval(account, target)
            except (RuntimeError, discord.DiscordException) as error:
                await asyncio.to_thread(self._accounts.delete_pending, account.id)
                await interaction.followup.send(
                    f"申請を送信できませんでした: {error}", ephemeral=True
                )
                return
            await interaction.followup.send(
                "参加申請を送信しました。管理者の承認をお待ちください。",
                ephemeral=True,
            )
            return

        try:
            await self._add_to_whitelist(account)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not add account through RCON: %s", error)
            await interaction.followup.send(
                "登録を保存しましたが、Minecraftへの反映待ちです。"
                f"Botが最大{WHITELIST_RETRY_LIMIT}回まで自動再試行します。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ **{discord.utils.escape_markdown(normalized_name)}** を登録しました。"
            "\nMinecraftサーバーへ参加できます。",
            ephemeral=True,
        )
        if source == "admin" and target.id != interaction.user.id:
            await self._notify_target(target, account)

    async def show_user_accounts(self, interaction: discord.Interaction) -> None:
        accounts = await asyncio.to_thread(
            self._accounts.list_for_discord_user, interaction.user.id
        )
        if not accounts:
            await interaction.response.send_message(
                "登録済みのMinecraftアカウントはありません。",
                ephemeral=True,
            )
            return
        lines = [self._account_line(account) for account in accounts]
        removable = [
            account
            for account in accounts
            if account.status in {"active", "pending_approval", "pending_add"}
            or (
                account.status == "pending_remove"
                and account.whitelist_retry_count >= WHITELIST_RETRY_LIMIT
            )
        ][:25]
        view = AccountSelectView(self, removable, "remove") if removable else None
        await interaction.response.send_message(
            "あなたのMinecraftアカウント\n\n" + "\n".join(lines),
            view=view,
            ephemeral=True,
        )

    async def show_unlinked_accounts(self, interaction: discord.Interaction) -> None:
        if not await self._import_whitelist():
            await interaction.response.send_message(
                "WhitelistのUUID情報を安全に取り込めなかったため、紐付け操作を停止しました。"
                "管理者がBotのログと登録状態を確認してください。",
                ephemeral=True,
            )
            return
        accounts = await asyncio.to_thread(self._accounts.list_unlinked)
        if not accounts:
            await interaction.response.send_message(
                "未連携の既存whitelistはありません。", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Discordアカウントへ紐付ける既存whitelistを選択してください。"
            "\n初期状態ではwhitelistから削除されない保護対象です。",
            view=AccountSelectView(self, accounts, "link"),
            ephemeral=True,
        )

    async def show_relinkable_accounts(self, interaction: discord.Interaction) -> None:
        if not await self._require_server_manager(interaction):
            return
        accounts = await asyncio.to_thread(self._accounts.list_relinkable)
        if not accounts:
            await interaction.response.send_message(
                "紐付け先を修正できるMinecraftアカウントはありません。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Discordの紐付け先を修正するMinecraftアカウントを選択してください。"
            "\n通常はWhitelistを変更しません。削除反映待ち・削除済みは再追加して復旧します。",
            view=AccountSelectView(self, accounts, "relink"),
            ephemeral=True,
        )

    async def show_pending_removal_corrections(self, interaction: discord.Interaction) -> None:
        if not await self._require_server_manager(interaction):
            return
        accounts = await asyncio.to_thread(self._accounts.list_pending_removal_corrections)
        if not accounts:
            await interaction.response.send_message(
                "Minecraft IDを修正できる削除反映待ちの登録はありません。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "誤って登録したMinecraft IDを選択してください。\n"
            "誤IDの削除は取り消さず、同じDiscordユーザーへ正しいIDを登録します。",
            view=AccountSelectView(self, accounts, "correct_id"),
            ephemeral=True,
        )

    async def confirm_minecraft_id_correction(
        self,
        interaction: discord.Interaction,
        account_id: int,
        minecraft_name: str,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        old_account = await asyncio.to_thread(self._accounts.get, account_id)
        if (
            old_account is None
            or old_account.discord_user_id is None
            or old_account.status not in {"pending_remove", "missing"}
        ):
            await interaction.response.send_message(
                "この登録はMinecraft ID修正の対象ではありません。",
                ephemeral=True,
            )
            return
        try:
            normalized_name, server_name = self._normalize_player_name(
                old_account.edition, minecraft_name
            )
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        if server_name.casefold() == old_account.server_player_name.casefold():
            await interaction.response.send_message(
                "現在削除中のMinecraft IDと同じです。正しい別のIDを入力してください。",
                ephemeral=True,
            )
            return
        existing = await asyncio.to_thread(self._accounts.get_by_server_player_name, server_name)
        if (
            existing is not None
            and existing.status != "missing"
            and existing.discord_user_id not in {None, old_account.discord_user_id}
        ):
            await interaction.response.send_message(
                "正しいMinecraft IDは別のDiscordユーザーに紐付いています。"
                "先にDiscord側の紐付け先修正を行ってください。",
                ephemeral=True,
            )
            return
        if existing is not None and existing.status not in {
            "active",
            "pending_add",
            "missing",
        }:
            await interaction.response.send_message(
                "正しいMinecraft IDには処理中の登録があります。処理完了後に再試行してください。",
                ephemeral=True,
            )
            return
        edition_label = "Java版" if old_account.edition == "java" else "Bedrock版"
        if existing is not None and existing.status == "active":
            registration_action = (
                "既存Whitelistを同じDiscordユーザーへ紐付けます。"
                if existing.discord_user_id is None
                else "正しいMinecraft IDはすでに同じDiscordユーザーへ登録済みです。"
            )
        elif existing is not None and existing.status == "pending_add":
            registration_action = "正しいMinecraft IDは追加反映待ちです。"
        else:
            registration_action = "正しいMinecraft IDを新しくWhitelistへ登録します。"
        old_registration_action = (
            "誤登録・削除済み" if old_account.status == "missing" else "誤登録・削除継続"
        )
        old_deletion_note = (
            "誤登録はすでに削除済みです。"
            if old_account.status == "missing"
            else "誤登録の削除反映待ちは取り消しません。"
        )
        await interaction.response.send_message(
            "次の内容でMinecraft IDを修正します。\n\n"
            f"Discord: <@{old_account.discord_user_id}>\n"
            f"エディション: **{edition_label}**\n"
            f"{old_registration_action}: "
            f"**{discord.utils.escape_markdown(old_account.minecraft_name)}**\n"
            f"正しいID: **{discord.utils.escape_markdown(normalized_name)}**\n\n"
            f"{registration_action}\n"
            f"{old_deletion_note}",
            view=ConfirmMinecraftIdCorrectionView(
                self,
                owner_id=interaction.user.id,
                old_account_id=old_account.id,
                expected_discord_user_id=old_account.discord_user_id,
                edition=old_account.edition,
                minecraft_name=normalized_name,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def correct_pending_removal_minecraft_id(
        self,
        interaction: discord.Interaction,
        *,
        old_account_id: int,
        expected_discord_user_id: int,
        edition: str,
        minecraft_name: str,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        await interaction.response.edit_message(
            content="⏳ Minecraft IDを確認しています…",
            view=None,
        )
        old_account = await asyncio.to_thread(self._accounts.get, old_account_id)
        if (
            old_account is None
            or old_account.discord_user_id != expected_discord_user_id
            or old_account.edition != edition
            or old_account.status not in {"pending_remove", "missing"}
        ):
            await interaction.edit_original_response(
                content="誤登録の状態または紐付け情報が変更されたため、最初からやり直してください。",
                view=None,
            )
            return
        try:
            normalized_name, server_name = self._normalize_player_name(edition, minecraft_name)
        except ValueError as error:
            await interaction.edit_original_response(content=str(error), view=None)
            return
        if server_name.casefold() == old_account.server_player_name.casefold():
            await interaction.edit_original_response(
                content="誤登録と同じMinecraft IDには修正できません。",
                view=None,
            )
            return
        try:
            server_name, player_uuid = await self._resolve_player_profile(
                edition=edition,
                minecraft_name=normalized_name,
                server_player_name=server_name,
            )
            normalized_name = (
                server_name.removeprefix(self._config.floodgate_username_prefix)
                if edition == "bedrock"
                else server_name
            )
            existing_by_uuid = await asyncio.to_thread(
                self._accounts.get_by_player_uuid, player_uuid
            )
            existing_by_name = await asyncio.to_thread(
                self._accounts.get_by_server_player_name, server_name
            )
        except (OSError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"Minecraftアカウントを確認できないため修正しませんでした: {error}",
                view=None,
            )
            return
        if (
            existing_by_uuid is not None
            and existing_by_name is not None
            and existing_by_uuid.id != existing_by_name.id
        ):
            await interaction.edit_original_response(
                content=(
                    "UUIDとプレイヤー名が別々の登録に一致しました。"
                    "安全のため変更していません。管理者が登録状態を確認してください。"
                ),
                view=None,
            )
            return
        existing = existing_by_uuid or existing_by_name
        discord_username = old_account.discord_username or str(expected_discord_user_id)
        if interaction.guild is not None:
            target = interaction.guild.get_member(expected_discord_user_id)
            if target is not None:
                discord_username = target.display_name
        try:
            same_identity = (
                old_account.player_uuid is not None
                and old_account.player_uuid.casefold() == player_uuid.casefold()
            )
            if same_identity:
                corrected = await asyncio.to_thread(
                    self._accounts.update_player_profile,
                    old_account.id,
                    minecraft_name=normalized_name,
                    server_player_name=server_name,
                    player_uuid=player_uuid,
                    status="pending_add",
                )
            elif existing is None or existing.status == "missing":
                corrected = await asyncio.to_thread(
                    self._accounts.create_registration,
                    edition=edition,
                    minecraft_name=normalized_name,
                    server_player_name=server_name,
                    discord_user_id=expected_discord_user_id,
                    discord_username=discord_username,
                    source="admin",
                    status="pending_add",
                    created_by=interaction.user.id,
                    player_uuid=player_uuid,
                )
            elif existing.status == "active" and existing.discord_user_id is None:
                existing = await asyncio.to_thread(
                    self._accounts.update_player_profile,
                    existing.id,
                    minecraft_name=normalized_name,
                    server_player_name=server_name,
                    player_uuid=player_uuid,
                )
                corrected = await asyncio.to_thread(
                    self._accounts.link_existing,
                    existing.id,
                    discord_user_id=expected_discord_user_id,
                    discord_username=discord_username,
                    managed=old_account.managed,
                    created_by=interaction.user.id,
                )
            elif existing.discord_user_id == expected_discord_user_id and existing.status in {
                "active",
                "pending_add",
            }:
                corrected = await asyncio.to_thread(
                    self._accounts.update_player_profile,
                    existing.id,
                    minecraft_name=normalized_name,
                    server_player_name=server_name,
                    player_uuid=player_uuid,
                )
            else:
                raise ValueError(
                    "正しいMinecraft IDは別の登録で使用されています。状態を確認してください。"
                )
        except ValueError as error:
            await interaction.edit_original_response(content=str(error), view=None)
            return

        add_error: OSError | RconError | RuntimeError | ValueError | None = None
        if corrected.status == "pending_add":
            try:
                await self._add_to_whitelist(corrected)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                add_error = error
        self._audit_server_action(
            interaction,
            "minecraft id correction "
            f"old_account_id={old_account.id} old_name={old_account.server_player_name!r} "
            f"new_account_id={corrected.id} new_name={server_name!r} "
            f"discord_user_id={expected_discord_user_id}",
        )
        if add_error is not None:
            retry_state = (
                "UUIDが同一のため削除待ちは取り消し、正しい表示名での追加を再試行します。"
                if same_identity
                else "誤登録の削除はそのまま継続し、正しいIDの追加を再試行します。"
            )
            await interaction.edit_original_response(
                content=(
                    f"⚠️ 正しいMinecraft ID "
                    f"**{discord.utils.escape_markdown(normalized_name)}** を保存しましたが、"
                    f"Whitelistへの追加は反映待ちです: {add_error}\n"
                    f"{retry_state}Botが最大{WHITELIST_RETRY_LIMIT}回まで自動再試行します。"
                ),
                view=None,
            )
            return
        if same_identity:
            deletion_state = "UUIDが同一のため、別登録を作らず表示名だけを更新しました。"
        else:
            deletion_state = (
                "誤登録は削除済みです。"
                if old_account.status == "missing"
                else "誤登録の削除反映待ちはそのまま継続します。"
            )
        await interaction.edit_original_response(
            content=(
                f"✅ <@{expected_discord_user_id}> に正しいMinecraft ID "
                f"**{discord.utils.escape_markdown(normalized_name)}** を登録しました。\n"
                f"{deletion_state}"
            ),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def link_existing_account(
        self,
        interaction: discord.Interaction,
        account_id: int,
        target: discord.Member,
        *,
        managed: bool,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        try:
            account = await asyncio.to_thread(
                self._accounts.link_existing,
                account_id,
                discord_user_id=target.id,
                discord_username=target.display_name,
                managed=managed,
                created_by=interaction.user.id,
            )
        except ValueError as error:
            await interaction.response.edit_message(content=str(error), view=None)
            return
        policy = "Discord退会時に自動削除" if managed else "whitelistを保護"
        await interaction.response.edit_message(
            content=(
                f"**{discord.utils.escape_markdown(account.minecraft_name)}** を "
                f"{target.mention} に紐付けました。\n管理方法: {policy}"
            ),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def confirm_account_relink(
        self,
        interaction: discord.Interaction,
        account_id: int,
        target: discord.Member,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        account = await asyncio.to_thread(self._accounts.get, account_id)
        if (
            account is None
            or account.discord_user_id is None
            or account.status not in {"active", "pending_add", "pending_remove", "missing"}
        ):
            await interaction.response.edit_message(
                content="このMinecraftアカウントの紐付け先は修正できません。",
                view=None,
            )
            return
        if account.discord_user_id == target.id:
            await interaction.response.edit_message(
                content=f"すでに {target.mention} に紐付いています。",
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        recovering_removal = account.status in {"pending_remove", "missing"}
        if account.status == "missing":
            recovery_text = "⚠️ 削除済みのため、紐付け先を変更してWhitelistへ再追加します。"
        elif account.status == "pending_remove":
            recovery_text = (
                "⚠️ 削除反映待ちを取り消し、Whitelistに残っているか確認して、"
                "削除済みなら再追加します。"
            )
        else:
            recovery_text = "WhitelistとMinecraft側の登録状態は変更しません。"
        await interaction.response.edit_message(
            content=(
                "次の内容でDiscordの紐付け先だけを変更します。\n\n"
                f"Minecraftアカウント: "
                f"**{discord.utils.escape_markdown(account.minecraft_name)}**\n"
                f"現在: <@{account.discord_user_id}>\n"
                f"変更後: {target.mention}\n\n"
                "管理方法は変更しません。\n"
                f"{recovery_text}\n"
                "すでに付与済み・送信待ちのXPは移動せず、変更後に発生したXPから"
                "新しいユーザーへ反映されます。"
            ),
            view=ConfirmRelinkView(
                self,
                owner_id=interaction.user.id,
                account_id=account.id,
                expected_discord_user_id=account.discord_user_id,
                target=target,
                recover_pending_remove=recovering_removal,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def reassign_account_link(
        self,
        interaction: discord.Interaction,
        *,
        account_id: int,
        expected_discord_user_id: int,
        target: discord.Member,
        recover_pending_remove: bool = False,
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        recovery_error: OSError | RconError | RuntimeError | ValueError | None = None
        try:
            if recover_pending_remove:
                async with self._whitelist_operation_lock:
                    account = await asyncio.to_thread(
                        self._accounts.reassign_discord_user,
                        account_id,
                        expected_discord_user_id=expected_discord_user_id,
                        discord_user_id=target.id,
                        discord_username=target.display_name,
                        recover_pending_remove=True,
                    )
                    try:
                        await self._add_to_whitelist_locked(account)
                    except (OSError, RconError, RuntimeError, ValueError) as error:
                        recovery_error = error
            else:
                account = await asyncio.to_thread(
                    self._accounts.reassign_discord_user,
                    account_id,
                    expected_discord_user_id=expected_discord_user_id,
                    discord_user_id=target.id,
                    discord_username=target.display_name,
                )
        except ValueError as error:
            await interaction.response.edit_message(content=str(error), view=None)
            return
        self._audit_server_action(
            interaction,
            "account relink "
            f"account_id={account.id} old_user_id={expected_discord_user_id} "
            f"new_user_id={target.id} recovered_removal={recover_pending_remove}",
        )
        if recovery_error is not None:
            await interaction.response.edit_message(
                content=(
                    f"⚠️ **{discord.utils.escape_markdown(account.minecraft_name)}** の削除予約を"
                    f"取り消し、紐付け先を {target.mention} へ変更しました。\n"
                    "MinecraftのWhitelistは再反映待ちです。Botが後から再試行します: "
                    f"{recovery_error}\n最大{WHITELIST_RETRY_LIMIT}回で自動再試行を停止します。"
                ),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        unchanged_or_recovered = (
            "管理方法は変更していません。\n"
            "削除予約を取り消し、Whitelistへの参加状態を復旧しました。"
            if recover_pending_remove
            else "Whitelistと管理方法は変更していません。"
        )
        await interaction.response.edit_message(
            content=(
                f"✅ **{discord.utils.escape_markdown(account.minecraft_name)}** の紐付け先を "
                f"<@{expected_discord_user_id}> から {target.mention} へ変更しました。\n"
                f"{unchanged_or_recovered}"
            ),
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def remove_account(self, interaction: discord.Interaction, account_id: int) -> None:
        account = await asyncio.to_thread(self._accounts.get, account_id)
        is_manager = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.manage_guild
        )
        if (account is None or account.discord_user_id != interaction.user.id) and not is_manager:
            await interaction.response.edit_message(
                content="このアカウントは解除できません。", view=None
            )
            return
        if account is None:
            await interaction.response.edit_message(content="登録が見つかりません。", view=None)
            return
        if not account.managed:
            await asyncio.to_thread(self._accounts.unlink_protected, account.id)
            await interaction.response.edit_message(
                content=(
                    f"**{discord.utils.escape_markdown(account.minecraft_name)}** の紐付けを"
                    "解除しました。既存whitelistは保護されています。"
                ),
                view=None,
            )
            return
        if account.status == "pending_approval":
            await asyncio.to_thread(self._accounts.delete_pending, account.id)
            await interaction.response.edit_message(content="申請を取り消しました。", view=None)
            return
        retrying_removal = account.status == "pending_remove"
        await interaction.response.defer()
        try:
            await self._remove_from_whitelist(account)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await asyncio.to_thread(self._accounts.update_status, account.id, "pending_remove")
            await interaction.edit_original_response(
                content=(
                    (
                        "Whitelistからの解除を再試行しましたが、反映できませんでした: "
                        if retrying_removal
                        else "解除を保存しましたが、Minecraftへの反映待ちです: "
                    )
                    + f"{error}\n"
                    f"最大{WHITELIST_RETRY_LIMIT}回で自動再試行を停止します。"
                ),
                view=None,
            )
            return
        await interaction.edit_original_response(
            content=(
                f"**{discord.utils.escape_markdown(account.minecraft_name)}** の"
                + (
                    "Whitelist解除を再試行し、完了しました。"
                    if retrying_removal
                    else "参加登録を解除しました。"
                )
            ),
            view=None,
        )

    async def process_approval(
        self, interaction: discord.Interaction, account_id: int, *, approved: bool
    ) -> None:
        if not await self._require_server_manager(interaction):
            return
        account = await asyncio.to_thread(self._accounts.get, account_id)
        if account is None or account.status != "pending_approval":
            await interaction.response.send_message(
                "この申請はすでに処理されています。", ephemeral=True
            )
            return
        if not approved:
            await asyncio.to_thread(self._accounts.update_status, account.id, "rejected")
            await interaction.response.edit_message(
                embed=self._approval_embed(account, "却下済み"),
                view=None,
            )
            return
        if account.discord_user_id is None:
            await interaction.response.send_message("申請者が見つかりません。", ephemeral=True)
            return
        guild = interaction.guild
        if guild is None:
            return
        try:
            target = guild.get_member(account.discord_user_id) or await guild.fetch_member(
                account.discord_user_id
            )
        except discord.NotFound:
            await interaction.response.send_message(
                "申請者はDiscordサーバーに参加していません。", ephemeral=True
            )
            return
        await interaction.response.defer()
        await asyncio.to_thread(self._accounts.update_status, account.id, "pending_add")
        try:
            await self._add_to_whitelist(account)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(
                f"Minecraftへ反映できませんでした: {error}\n"
                f"Botが最大{WHITELIST_RETRY_LIMIT}回まで自動再試行します。",
                ephemeral=True,
            )
            return
        await interaction.edit_original_response(
            embed=self._approval_embed(account, "承認済み"),
            view=None,
        )
        await self._notify_target(target, account)

    async def show_admin_summary(self, interaction: discord.Interaction) -> None:
        registered, unlinked, pending = await asyncio.to_thread(self._accounts.count_summary)
        registrations = await asyncio.to_thread(self._accounts.list_whitelist_registrations)
        try:
            player_profiles = await asyncio.to_thread(
                read_whitelisted_profiles,
                self._config.minecraft_whitelist_path,
            )
            present_names = {player.name.casefold() for player in player_profiles}
            present_uuids = {player.player_uuid.casefold() for player in player_profiles}
            unreflected = sum(
                account.status in {"active", "pending_add"}
                and (
                    account.player_uuid.casefold() not in present_uuids
                    if account.player_uuid is not None
                    else account.server_player_name.casefold() not in present_names
                )
                for account in registrations
            )
            actual_line = (
                f"実Whitelist: **{len(player_profiles)}件**\n未反映: **{unreflected}件**\n"
            )
        except ValueError:
            actual_line = "実Whitelist: **取得失敗**\n"
        await interaction.response.send_message(
            f"登録情報: **{registered}件**\n"
            f"{actual_line}"
            f"未連携・保護: **{unlinked}件**\n"
            f"承認待ち: **{pending}件**",
            ephemeral=True,
        )

    async def show_whitelist_entries(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            player_profiles = await asyncio.to_thread(
                read_whitelisted_profiles,
                self._config.minecraft_whitelist_path,
            )
        except ValueError as error:
            await interaction.followup.send(
                f"Whitelist一覧を取得できませんでした: {error}",
                ephemeral=True,
            )
            return

        registrations = await asyncio.to_thread(self._accounts.list_whitelist_registrations)
        registrations_by_name = {
            account.server_player_name.casefold(): account for account in registrations
        }
        registrations_by_uuid: dict[str, list[MinecraftAccount]] = {}
        for account in registrations:
            if account.player_uuid is not None:
                registrations_by_uuid.setdefault(account.player_uuid.casefold(), []).append(account)
        entries: list[tuple[str, MinecraftAccount | None, bool]] = []
        included_account_ids: set[int] = set()
        for profile in player_profiles:
            uuid_matches = registrations_by_uuid.get(profile.player_uuid.casefold(), [])
            account = (
                uuid_matches[0]
                if len(uuid_matches) == 1
                else registrations_by_name.get(profile.name.casefold())
            )
            if account is not None:
                included_account_ids.add(account.id)
            entries.append((profile.name, account, True))
        actual_uuids = {profile.player_uuid.casefold() for profile in player_profiles}
        actual_names = {profile.name.casefold() for profile in player_profiles}
        for account in registrations:
            if account.id in included_account_ids:
                continue
            is_present = (
                account.player_uuid.casefold() in actual_uuids
                if account.player_uuid is not None
                else account.server_player_name.casefold() in actual_names
            )
            entries.append((account.server_player_name, account, is_present))
        entries.sort(key=lambda entry: entry[0].casefold())

        if not entries:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🛡️ Whitelist一覧",
                    description="登録者はいません。",
                    color=discord.Color.blurple(),
                ),
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for player_name, account, is_present in entries:
            edition = (
                account.edition
                if account is not None
                else (
                    "bedrock"
                    if self._config.floodgate_username_prefix
                    and player_name.startswith(self._config.floodgate_username_prefix)
                    else "java"
                )
            )
            edition_label = "🪨 Bedrock" if edition == "bedrock" else "☕ Java"
            escaped_name = discord.utils.escape_markdown(player_name)
            if account is not None and account.discord_user_id is not None:
                account_text = f"**{escaped_name} (<@{account.discord_user_id}>)**"
            else:
                account_text = f"**{escaped_name}** (未連携)"
            state = ""
            if (
                account is not None
                and account.whitelist_retry_count >= WHITELIST_RETRY_LIMIT
                and account.status == "pending_add"
            ):
                state = "  ⚠️ Whitelist追加失敗\uff08自動再試行停止\uff09"
            elif (
                account is not None
                and account.whitelist_retry_count >= WHITELIST_RETRY_LIMIT
                and account.status == "pending_remove"
            ):
                state = "  ⚠️ Whitelist解除失敗\uff08自動再試行停止\uff09"
            elif not is_present:
                state = "  ⚠️ Whitelist未反映"
            elif account is not None and account.status == "pending_remove":
                state = "  ⚠️ 削除反映待ち"
            lines.append(f"{edition_label}  {account_text}{state}")

        embeds: list[discord.Embed] = []
        total = len(lines)
        actual_count = len(player_profiles)
        registered_count = len(registrations)
        for offset in range(0, total, 20):
            page = discord.Embed(
                title=(
                    f"🛡️ Whitelist一覧 (実登録{actual_count}件 / 登録情報{registered_count}件)"
                    if offset == 0
                    else "🛡️ Whitelist一覧 (続き)"
                ),
                description="\n".join(lines[offset : offset + 20]),
                color=discord.Color.blurple(),
            )
            page.set_footer(text=f"{offset + 1}-{min(offset + 20, total)} / {total}")
            embeds.append(page)

        for embed in embeds:
            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.guild_only()
    async def _voice_command(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        voice_client = guild.voice_client if guild is not None else None
        if voice_client is not None and voice_client.is_connected():
            await self.disconnect_voice(interaction)
            return
        member = interaction.user
        voice_state = member.voice if isinstance(member, discord.Member) else None
        channel = voice_state.channel if voice_state is not None else None
        if isinstance(channel, discord.VoiceChannel):
            await self.configure_voice_channel(interaction, channel)
            return
        await interaction.response.send_message(
            "先に接続させたいVCへ参加してから `/vc` を実行してください。",
            ephemeral=True,
        )

    async def show_voice_controls(self, interaction: discord.Interaction) -> None:
        channel_id = self._settings.voice_channel_id
        status = "停止中"
        if self._settings.voice_enabled and channel_id is not None:
            status = f"接続先: <#{channel_id}>"
        api_status = "設定済み" if self._voice_player.configured else "APIトークン未設定"
        await interaction.response.send_message(
            f"Minecraft読み上げ: **{status}**\nVOICEVOX API: **{api_status}**\n\n"
            "接続先のVCを選択すると、チャット・参加・退出・進捗・死亡を読み上げます。",
            view=VoiceControlView(self, interaction.user.id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def configure_voice_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not self._voice_player.configured:
            await interaction.edit_original_response(
                content="VOICEVOX_TTS_API_TOKENが設定されていません。",
                view=None,
            )
            return
        try:
            self._ensure_same_guild(channel.guild.id)
            await self._connect_voice_channel(channel)
            await self._save_settings(
                replace(
                    self._settings,
                    voice_channel_id=channel.id,
                    voice_enabled=True,
                )
            )
            self._voice_player.enqueue(channel.guild.id, _VOICE_CONNECTED_SPEECH)
        except (OSError, RuntimeError, discord.DiscordException) as error:
            await interaction.edit_original_response(
                content=f"VCへ接続できませんでした: {error}",
                view=None,
            )
            return
        self._audit_server_action(interaction, f"voice connect channel_id={channel.id}")
        await interaction.edit_original_response(
            content=f"✅ {channel.mention} でMinecraft読み上げを開始しました。",
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🔊 Minecraft読み上げを開始しました",
                    description=(
                        f"{channel.mention} で、Minecraftサーバーのチャット・参加・退出・"
                        "進捗・死亡を読み上げます。\n話者は **小夜/SAYO** です。"
                    ),
                    color=discord.Color.green(),
                ),
                ephemeral=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.DiscordException as error:
            LOGGER.warning("Could not post Minecraft voice connection notice: %s", error)
            await interaction.edit_original_response(
                content=(
                    f"✅ {channel.mention} でMinecraft読み上げを開始しました。\n"
                    "⚠️ 接続案内をこのチャンネルへ投稿できませんでした。"
                ),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def disconnect_voice(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        try:
            if guild is not None and guild.voice_client is not None:
                await guild.voice_client.disconnect(force=True)
            await self._save_settings(
                replace(
                    self._settings,
                    voice_channel_id=None,
                    voice_enabled=False,
                )
            )
        except (OSError, discord.DiscordException) as error:
            await interaction.edit_original_response(
                content=f"読み上げを停止できませんでした: {error}",
                view=None,
            )
            return
        self._audit_server_action(interaction, "voice disconnect")
        await interaction.edit_original_response(
            content="Minecraft読み上げを停止し、VCから切断しました。",
            view=None,
        )

    async def test_voice(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if (
            not self._settings.voice_enabled
            or guild_id is None
            or not self._voice_player.is_connected(guild_id)
        ):
            await interaction.response.send_message(
                "VCへ接続されていません。接続先を選び直してください。",
                ephemeral=True,
            )
            return
        if not self._voice_player.enqueue(guild_id, _VOICE_CHECK_SPEECH):
            await interaction.response.send_message(
                "読み上げキューへ追加できませんでした。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "読み上げ確認音声をキューへ追加しました。", ephemeral=True
        )

    async def validate_runtime_admin(self, interaction: discord.Interaction) -> bool:
        return await self._require_server_manager(interaction)

    async def show_server_control(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            embed = await self._server_control_embed()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(
                f"Minecraftサーバーの状態を取得できませんでした: {error}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=embed,
            view=ServerControlView(self, interaction.user.id),
            ephemeral=True,
        )

    async def refresh_server_control(
        self,
        interaction: discord.Interaction,
        view: ServerControlView,
    ) -> None:
        await interaction.response.defer()
        try:
            embed = await self._server_control_embed()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"更新できませんでした: {error}", ephemeral=True)
            return
        await interaction.edit_original_response(embed=embed, view=view)

    async def show_online_players(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            players = await self._online_players()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"取得できませんでした: {error}", ephemeral=True)
            return
        description = (
            "オンラインプレイヤーはいません。"
            if not players
            else "\n".join(f"・**{discord.utils.escape_markdown(name)}**" for name in players)
        )
        await interaction.followup.send(
            embed=discord.Embed(
                title=f"👥 オンライン {len(players)}人",
                description=description,
                color=discord.Color.green() if players else discord.Color.light_grey(),
            ),
            ephemeral=True,
        )

    async def show_kick_player_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            players = await self._online_players()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"取得できませんでした: {error}", ephemeral=True)
            return
        if not players:
            await interaction.followup.send("オンラインプレイヤーはいません。", ephemeral=True)
            return
        await interaction.followup.send(
            "キックするプレイヤーを選択してください。",
            view=KickPlayerSelectView(self, interaction.user.id, players[:25]),
            ephemeral=True,
        )

    async def kick_online_player(
        self,
        interaction: discord.Interaction,
        player_name: str,
        reason: str,
    ) -> None:
        await interaction.response.defer()
        try:
            players = await self._online_players()
            if player_name not in players:
                raise ValueError("そのプレイヤーはすでにオフラインです")
            await self._execute_checked_rcon(kick_command(player_name, reason))
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"キックできませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, f"kick player={player_name}")
        await interaction.edit_original_response(
            content=f"✅ **{discord.utils.escape_markdown(player_name)}** をキックしました。",
            view=None,
        )

    async def announce_server(self, interaction: discord.Interaction, message: str) -> None:
        if not await self.validate_runtime_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self._execute_checked_rcon(announcement_command(message))
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"告知できませんでした: {error}", ephemeral=True)
            return
        self._audit_server_action(interaction, "announcement")
        guild_id = interaction.guild_id
        if guild_id is not None:
            self._voice_player.enqueue(guild_id, announcement_speech_text(message))
        try:
            await self._send(format_server_announcement(message))
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not log Minecraft server announcement in Discord: %s", error)
            await interaction.followup.send(
                "⚠️ サーバー内へ告知しましたが、チャンネルログへ投稿できませんでした。",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "✅ サーバー内へ告知し、チャンネルログにも投稿しました。",
            ephemeral=True,
        )

    async def show_whitelist_controls(self, interaction: discord.Interaction) -> None:
        try:
            state = await self._whitelist_state_text()
        except ValueError as error:
            await interaction.response.send_message(
                f"Whitelistの実状態を取得できませんでした: {error}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Whitelistの現在状態: **{state}**\n\n停止中はwhitelist未登録者も接続できます。",
            view=WhitelistControlView(self, interaction.user.id),
            ephemeral=True,
        )

    async def pause_whitelist(self, interaction: discord.Interaction, minutes: int) -> None:
        if minutes not in {15, 30, 60}:
            await interaction.response.send_message("無効な停止時間です。", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            resume_at = await self._pause_whitelist_for(minutes)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"停止できませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, f"whitelist pause minutes={minutes}")
        await interaction.edit_original_response(
            content=(
                f"⚠️ Whitelistを{minutes}分間停止しました。<t:{int(resume_at)}:R>に自動再開します。"
            ),
            view=None,
        )

    async def resume_whitelist(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            await self._resume_whitelist_now()
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"再開できませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, "whitelist resume")
        await interaction.edit_original_response(content="✅ Whitelistを再開しました。", view=None)

    async def change_world(
        self,
        interaction: discord.Interaction,
        command: str,
        description: str,
    ) -> None:
        allowed = {
            "weather clear": "天候を晴れ",
            "weather rain": "天候を雨",
            "weather thunder": "天候を雷雨",
            "time set day": "時刻を朝",
            "time set night": "時刻を夜",
        }
        if allowed.get(command) != description:
            await interaction.response.send_message("許可されていない操作です。", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self._execute_checked_rcon(command)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.edit_original_response(
                content=f"変更できませんでした: {error}", view=None
            )
            return
        self._audit_server_action(interaction, command)
        await interaction.edit_original_response(
            content=f"✅ {description}に変更しました。", view=None
        )

    async def show_performance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            response = await self._execute_checked_rcon("spark health --memory")
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"取得できませんでした: {error}", ephemeral=True)
            return
        output = clean_rcon_output(response, limit=3800).replace("```", "'''")
        await interaction.followup.send(
            embed=discord.Embed(
                title="📊 Minecraftパフォーマンス",
                description=f"```text\n{output or '応答がありませんでした'}\n```",
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )

    async def _server_control_embed(self) -> discord.Embed:
        players = await self._online_players()
        whitelist = await self._whitelist_state_text()
        names = (
            "、".join(discord.utils.escape_markdown(name) for name in players)
            if players
            else "なし"
        )
        embed = discord.Embed(
            title="🎮 Minecraft サーバー操作",
            description="🟢 サーバー稼働中",
            color=discord.Color.green(),
        )
        embed.add_field(name="オンライン", value=f"**{len(players)}人**", inline=True)
        embed.add_field(name="Whitelist", value=whitelist, inline=True)
        embed.add_field(name="プレイヤー", value=names[:1024], inline=False)
        embed.set_footer(text="表示内容はボタンを押した時点の状態です")
        return embed

    async def _online_players(self) -> list[str]:
        return parse_online_players(await self._execute_rcon("list"))

    async def _refresh_online_player_cache(self) -> None:
        if self._rcon is None:
            self._online_player_names.clear()
            return
        try:
            self._online_player_names = {name.casefold() for name in await self._online_players()}
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not initialize Minecraft online-player cache: %s", error)

    async def _execute_rcon(self, command: str) -> str:
        return await asyncio.to_thread(self._require_rcon().execute, command)

    async def _execute_checked_rcon(self, command: str) -> str:
        return validate_rcon_response(await self._execute_rcon(command))

    async def _read_whitelist_enabled(self) -> bool:
        return await asyncio.to_thread(
            read_whitelist_enabled,
            self._config.minecraft_server_properties_path,
        )

    async def _wait_for_whitelist_state(self, expected: bool) -> None:
        for attempt in range(10):
            if await self._read_whitelist_enabled() is expected:
                return
            if attempt < 9:
                await asyncio.sleep(0.2)
        label = "有効" if expected else "無効"
        raise RuntimeError(f"Whitelistの実状態が{label}になりませんでした")

    async def _set_whitelist_enabled(self, enabled: bool) -> None:
        command = "whitelist on" if enabled else "whitelist off"
        await self._execute_checked_rcon(command)
        await self._wait_for_whitelist_state(enabled)

    async def _pause_whitelist_for(self, minutes: int) -> float:
        async with self._whitelist_operation_lock:
            resume_at = time.time() + minutes * 60
            await self._save_settings(replace(self._settings, whitelist_resume_at=resume_at))
            try:
                await self._set_whitelist_enabled(False)
            except OSError, RconError, RuntimeError, ValueError:
                try:
                    await self._set_whitelist_enabled(True)
                except OSError, RconError, RuntimeError, ValueError:
                    LOGGER.exception(
                        "Whitelist pause failed and immediate safety recovery also failed"
                    )
                    raise
                await self._save_settings(replace(self._settings, whitelist_resume_at=None))
                raise
            return resume_at

    async def _resume_whitelist_now(self) -> None:
        async with self._whitelist_operation_lock:
            await self._set_whitelist_enabled(True)
            await self._save_settings(replace(self._settings, whitelist_resume_at=None))

    async def _whitelist_state_text(self) -> str:
        async with self._whitelist_operation_lock:
            enabled = await self._read_whitelist_enabled()
            resume_at = self._settings.whitelist_resume_at
        if not enabled and resume_at is not None:
            return f"一時停止中・<t:{int(resume_at)}:R>に自動再開"
        if not enabled:
            return "⚠️ 無効・自動再開予定なし"
        if resume_at is not None:
            return f"有効・一時停止の再反映待ち (<t:{int(resume_at)}:R>に再開)"
        return "有効"

    async def _resume_whitelist_if_due(self) -> None:
        async with self._whitelist_operation_lock:
            resume_at = self._settings.whitelist_resume_at
            if resume_at is None:
                return
            try:
                if time.time() < resume_at:
                    if await self._read_whitelist_enabled():
                        await self._set_whitelist_enabled(False)
                    return
                await self._set_whitelist_enabled(True)
                await self._save_settings(replace(self._settings, whitelist_resume_at=None))
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning("Could not reconcile Minecraft whitelist pause: %s", error)
                return
            LOGGER.info("Minecraft whitelist automatically resumed")

    @staticmethod
    def _audit_server_action(interaction: discord.Interaction, action: str) -> None:
        LOGGER.info(
            "Minecraft admin action user_id=%d guild_id=%s action=%s",
            interaction.user.id,
            interaction.guild_id,
            action,
        )

    async def _post_approval(self, account: MinecraftAccount, target: discord.Member) -> None:
        channel_id = self._settings.approval_channel_id
        if channel_id is None:
            raise RuntimeError("申請確認先が設定されていません")
        channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
        message = await channel.send(
            embed=self._approval_embed(account, "承認待ち", target),
            view=ApprovalView(self, account.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await asyncio.to_thread(self._accounts.set_approval_message, account.id, message.id)

    def _approval_embed(
        self,
        account: MinecraftAccount,
        state: str,
        target: discord.Member | None = None,
    ) -> discord.Embed:
        username = target.display_name if target is not None else account.discord_username or "不明"
        user_id = target.id if target is not None else account.discord_user_id
        edition = "Java版" if account.edition == "java" else "Bedrock版"
        color = (
            discord.Color.orange()
            if state == "承認待ち"
            else discord.Color.green()
            if state == "承認済み"
            else discord.Color.red()
        )
        return discord.Embed(
            title=f"Minecraft参加申請・{state}",
            description=(
                f"Discord: @{discord.utils.escape_markdown(username)} (`{user_id}`)\n"
                f"エディション: {edition}\n"
                f"アカウント名: **{discord.utils.escape_markdown(account.minecraft_name)}**"
            ),
            color=color,
        )

    async def _add_to_whitelist(self, account: MinecraftAccount) -> None:
        async with self._whitelist_operation_lock:
            await self._add_to_whitelist_locked(account)

    async def _add_to_whitelist_locked(self, account: MinecraftAccount) -> None:
        player_name, player_uuid = await self._resolve_whitelist_profile(account)
        command = (
            f"whitelist add {player_name}"
            if account.edition == "java"
            else f"fwhitelist add {player_uuid}"
        )
        await self._ensure_player_whitelist_state_locked(
            account,
            player_name=player_name,
            player_uuid=player_uuid,
            expected=True,
            command=command,
        )
        await asyncio.to_thread(self._accounts.update_status, account.id, "active")

    async def _remove_from_whitelist(self, account: MinecraftAccount) -> None:
        async with self._whitelist_operation_lock:
            if account.status == "pending_remove":
                current = await asyncio.to_thread(self._accounts.get, account.id)
                if current is None or current.status != "pending_remove":
                    return
            await self._remove_from_whitelist_locked(account)

    async def _remove_from_whitelist_locked(self, account: MinecraftAccount) -> None:
        rcon = self._require_rcon()
        player_name, player_uuid = await self._resolve_whitelist_profile(account)
        command = (
            f"whitelist remove {player_name}"
            if account.edition == "java"
            else f"fwhitelist remove {player_uuid}"
        )
        await self._ensure_player_whitelist_state_locked(
            account,
            player_name=player_name,
            player_uuid=player_uuid,
            expected=False,
            command=command,
        )
        kick_name = (
            player_name
            if account.edition == "java"
            else await self._cached_player_name_by_uuid(player_uuid)
        )
        if kick_name is not None:
            try:
                await asyncio.to_thread(
                    rcon.execute,
                    f'kick "{kick_name}" Discordの参加登録が解除されました',
                )
            except OSError, RconError:
                LOGGER.debug("Could not kick %s; player may be offline", kick_name)
        else:
            LOGGER.info(
                "Skipped kick because the current player name could not be verified for UUID %s",
                player_uuid,
            )
        await asyncio.to_thread(self._accounts.update_status, account.id, "missing")

    async def _ensure_player_whitelist_state_locked(
        self,
        account: MinecraftAccount,
        *,
        player_name: str,
        player_uuid: str,
        expected: bool,
        command: str,
    ) -> None:
        if await self._player_is_whitelisted(player_name, player_uuid) is expected:
            return
        response = await self._execute_checked_rcon(command)
        for attempt in range(20):
            if await self._player_is_whitelisted(player_name, player_uuid) is expected:
                return
            if attempt < 19:
                await asyncio.sleep(0.25)
        if expected:
            LOGGER.warning(
                "RCON whitelist add was not reflected for %s; using direct JSON fallback "
                "response=%r",
                account.minecraft_name,
                response,
            )
            await self._add_to_whitelist_file(account, player_name, player_uuid)
            return
        LOGGER.warning(
            "RCON whitelist remove was not reflected for %s; using direct JSON fallback "
            "response=%r",
            account.minecraft_name,
            response,
        )
        await self._remove_from_whitelist_file(player_name, player_uuid)
        return

    async def _add_to_whitelist_file(
        self, account: MinecraftAccount, player_name: str, player_uuid: str
    ) -> None:
        await asyncio.to_thread(
            upsert_whitelisted_player,
            self._config.minecraft_whitelist_path,
            player_name,
            player_uuid,
        )
        await self._execute_checked_rcon("whitelist reload")
        if not await self._player_is_whitelisted(player_name, player_uuid):
            raise RuntimeError(f"{player_name}のWhitelist直接追加を確認できませんでした")

    async def _remove_from_whitelist_file(self, player_name: str, player_uuid: str) -> None:
        await asyncio.to_thread(
            remove_whitelisted_player,
            self._config.minecraft_whitelist_path,
            player_uuid,
        )
        await self._execute_checked_rcon("whitelist reload")
        if await self._player_is_whitelisted(player_name, player_uuid):
            raise RuntimeError(f"{player_name}のWhitelist直接削除を確認できませんでした")

    async def _resolve_whitelist_profile(self, account: MinecraftAccount) -> tuple[str, str]:
        player_name, player_uuid = await self._resolve_player_profile(
            edition=account.edition,
            minecraft_name=account.minecraft_name,
            server_player_name=account.server_player_name,
            stored_uuid=account.player_uuid,
        )
        if (
            player_name.casefold() != account.server_player_name.casefold()
            or player_uuid.casefold() != (account.player_uuid or "").casefold()
        ):
            minecraft_name = (
                player_name.removeprefix(self._config.floodgate_username_prefix)
                if account.edition == "bedrock"
                else player_name
            )
            await asyncio.to_thread(
                self._accounts.update_player_profile,
                account.id,
                minecraft_name=minecraft_name,
                server_player_name=player_name,
                player_uuid=player_uuid,
            )
        return player_name, player_uuid

    async def _resolve_player_profile(
        self,
        *,
        edition: str,
        minecraft_name: str,
        server_player_name: str,
        stored_uuid: str | None = None,
    ) -> tuple[str, str]:
        normalized_stored_uuid: str | None = None
        if stored_uuid:
            try:
                normalized_stored_uuid = str(uuid.UUID(stored_uuid))
            except ValueError:
                LOGGER.warning("Ignoring invalid stored Minecraft UUID for %s", minecraft_name)

        try:
            whitelist_profiles = await asyncio.to_thread(
                read_whitelisted_profiles,
                self._config.minecraft_whitelist_path,
            )
        except ValueError:
            whitelist_profiles = []
        if normalized_stored_uuid is not None:
            if edition == "java":
                return await self._resolve_java_profile_by_uuid(normalized_stored_uuid)
            cached_name = await self._cached_player_name_by_uuid(normalized_stored_uuid)
            if cached_name is not None:
                return cached_name, normalized_stored_uuid
            uuid_matches = []
            for profile in whitelist_profiles:
                try:
                    profile_uuid = str(uuid.UUID(profile.player_uuid))
                except ValueError:
                    continue
                if profile_uuid.casefold() == normalized_stored_uuid.casefold():
                    uuid_matches.append(profile.name)
            if len({name.casefold() for name in uuid_matches}) > 1:
                raise ValueError(
                    f"Whitelistで同じUUIDが複数のプレイヤー名に一致しています: "
                    f"{normalized_stored_uuid}"
                )
            return (uuid_matches[0] if uuid_matches else server_player_name), normalized_stored_uuid
        normalized_server_name = server_player_name.casefold()
        for profile in whitelist_profiles:
            if profile.name.casefold() == normalized_server_name:
                try:
                    return profile.name, str(uuid.UUID(profile.player_uuid))
                except ValueError:
                    break
        cached_profile = await asyncio.to_thread(
            read_cached_player_profile,
            self._config.minecraft_whitelist_path.with_name("usercache.json"),
            server_player_name,
        )
        if cached_profile is not None:
            return cached_profile

        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if edition == "java":
                    url = _MOJANG_PROFILE_URL + quote(minecraft_name, safe="")
                    async with session.get(url) as response:
                        if response.status in {204, 404}:
                            raise ValueError(
                                f"Java版アカウント {minecraft_name} が存在しません。"
                                "エディションと名前を確認してください"
                            )
                        if response.status != 200:
                            raise RuntimeError(f"Mojang UUID API error {response.status}")
                        payload = await response.json(content_type=None)
                    raw_uuid = payload.get("id") if isinstance(payload, dict) else None
                    canonical_name = payload.get("name") if isinstance(payload, dict) else None
                    if not isinstance(raw_uuid, str) or not isinstance(canonical_name, str):
                        raise RuntimeError("Mojang UUID APIの応答形式が正しくありません")
                    try:
                        player_uuid = str(uuid.UUID(raw_uuid))
                    except ValueError as error:
                        raise RuntimeError("Mojang UUID APIのUUIDが正しくありません") from error
                    player_name = canonical_name
                else:
                    url = _GEYSER_XUID_URL + quote(minecraft_name, safe="")
                    async with session.get(url) as response:
                        payload = (
                            await response.json(content_type=None) if response.status == 200 else {}
                        )
                    raw_xuid = payload.get("xuid") if isinstance(payload, dict) else None
                    canonical_name: str | None = None
                    if not isinstance(raw_xuid, str):
                        url = _PLAYERDB_XBOX_URL + quote(minecraft_name, safe="")
                        async with session.get(url) as response:
                            if response.status != 200:
                                raise ValueError(
                                    f"Bedrock版アカウント {minecraft_name} "
                                    "が存在しません。ゲーマータグを確認してください"
                                )
                            payload = await response.json(content_type=None)
                        player = (
                            payload.get("data", {}).get("player")
                            if isinstance(payload, dict) and isinstance(payload.get("data"), dict)
                            else None
                        )
                        raw_xuid = player.get("id") if isinstance(player, dict) else None
                        canonical_name = (
                            player.get("username") if isinstance(player, dict) else None
                        )
                    try:
                        xuid = int(raw_xuid)
                    except (TypeError, ValueError) as error:
                        raise ValueError(
                            f"Bedrock版アカウント {minecraft_name} が存在しません。"
                            "ゲーマータグを確認してください"
                        ) from error
                    if not 0 <= xuid < 2**64:
                        raise RuntimeError("Geyser XUID APIのXUIDが正しくありません")
                    player_uuid = str(uuid.UUID(int=xuid))
                    if isinstance(canonical_name, str) and canonical_name:
                        normalized_name = canonical_name.replace(" ", "_")
                        player_name = f"{self._config.floodgate_username_prefix}{normalized_name}"
                    else:
                        player_name = server_player_name
        except (TimeoutError, aiohttp.ClientError) as error:
            raise RuntimeError(f"MinecraftアカウントUUID APIへ接続できません: {error}") from error

        return player_name, player_uuid

    async def _resolve_java_profile_by_uuid(self, player_uuid: str) -> tuple[str, str]:
        url = _MOJANG_SESSION_PROFILE_URL + player_uuid.replace("-", "")
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(url) as response,
            ):
                if response.status in {204, 404}:
                    raise ValueError(
                        f"Java版UUID {player_uuid} のアカウントが存在しません。"
                        "管理者が登録状態を確認してください"
                    )
                if response.status != 200:
                    raise RuntimeError(f"Mojang Session API error {response.status}")
                payload = await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise RuntimeError(f"Mojang Session APIへ接続できません: {error}") from error
        raw_uuid = payload.get("id") if isinstance(payload, dict) else None
        canonical_name = payload.get("name") if isinstance(payload, dict) else None
        try:
            response_uuid = str(uuid.UUID(raw_uuid))
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError("Mojang Session APIのUUIDが正しくありません") from error
        if response_uuid.casefold() != player_uuid.casefold():
            raise RuntimeError("Mojang Session APIのUUIDが要求したUUIDと一致しません")
        if not isinstance(canonical_name, str) or not _JAVA_NAME.fullmatch(canonical_name):
            raise RuntimeError("Mojang Session APIのプレイヤー名が正しくありません")
        return canonical_name, response_uuid

    async def _cached_player_name_by_uuid(self, player_uuid: str) -> str | None:
        profile = await asyncio.to_thread(
            read_cached_player_profile_by_uuid,
            self._config.minecraft_whitelist_path.with_name("usercache.json"),
            player_uuid,
        )
        return profile[0] if profile is not None else None

    async def _player_is_whitelisted(
        self, player_name: str, player_uuid: str | None = None
    ) -> bool:
        profiles = await asyncio.to_thread(
            read_whitelisted_profiles,
            self._config.minecraft_whitelist_path,
        )
        if player_uuid is not None:
            normalized_uuid = str(uuid.UUID(player_uuid)).casefold()
            return any(profile.player_uuid.casefold() == normalized_uuid for profile in profiles)
        normalized_name = player_name.casefold()
        return any(profile.name.casefold() == normalized_name for profile in profiles)

    def _require_rcon(self) -> RconClient:
        if self._rcon is None:
            raise RuntimeError("Minecraft RCONが設定されていません")
        return self._rcon

    def _normalize_player_name(self, edition: str, value: str) -> tuple[str, str]:
        name = value.strip()
        if edition == "java":
            if not _JAVA_NAME.fullmatch(name):
                raise ValueError(
                    "Java版の名前は3から16文字の半角英数字またはアンダースコアで入力してください。"
                )
            return name, name
        name = name.removeprefix(self._config.floodgate_username_prefix).strip()
        suffix_match = _MODERN_GAMERTAG_SUFFIX.fullmatch(name)
        if suffix_match is not None:
            base, suffix = suffix_match.groups()
            name = base.rstrip() + suffix.translate(_FULLWIDTH_DIGITS)
        if "#" in name or "\uff03" in name:
            raise ValueError("Bedrock版の # は末尾の数字サフィックスを含めて入力してください。")
        if not 1 <= len(name) <= 32 or any(
            character in name for character in ('"', "\\", "\n", "\r", "\0")
        ):
            raise ValueError("Bedrock版のゲーマータグを正しく入力してください。")
        server_name = f"{self._config.floodgate_username_prefix}{name.replace(' ', '_')}"
        return name, server_name

    async def _notify_target(self, target: discord.Member, account: MinecraftAccount) -> None:
        try:
            await target.send(
                "✅ Minecraftアカウント "
                f"**{discord.utils.escape_markdown(account.minecraft_name)}**"
                " の参加登録が完了しました。",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.DiscordException:
            LOGGER.info("Could not DM registration result to Discord user %d", target.id)

    async def _import_whitelist(self) -> bool:
        try:
            await asyncio.to_thread(
                self._accounts.import_whitelist,
                self._config.minecraft_whitelist_path,
                self._config.floodgate_username_prefix,
            )
        except (OSError, ValueError) as error:
            LOGGER.warning("Could not import Minecraft whitelist: %s", error)
            return False
        return True

    async def _sync_whitelist_accounts(self) -> None:
        if not await self._import_whitelist():
            return
        try:
            player_profiles = await asyncio.to_thread(
                read_whitelisted_profiles,
                self._config.minecraft_whitelist_path,
            )
        except ValueError as error:
            LOGGER.warning("Could not reconcile Minecraft whitelist registrations: %s", error)
            return
        changes = await asyncio.to_thread(
            self._accounts.reconcile_whitelist,
            [(player.name, player.player_uuid) for player in player_profiles],
        )
        if any(changes):
            LOGGER.info(
                "Reconciled Minecraft whitelist registrations queued_adds=%d "
                "completed_adds=%d completed_removals=%d",
                *changes,
            )
        await self._reconcile_pending_actions()

    async def _refresh_access_panel(self) -> None:
        channel_id = self._settings.panel_channel_id
        message_id = self._settings.panel_message_id
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=access_panel_embed(self._settings.approval_mode),
                view=AccessPanelView(self),
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not refresh access panel: %s", error)

    async def _refresh_admin_panel(self) -> None:
        channel_id = self._settings.admin_panel_channel_id
        message_id = self._settings.admin_panel_message_id
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            message = await channel.fetch_message(message_id)
            await message.edit(embed=admin_panel_embed(), view=AdminPanelView(self))
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not refresh admin panel: %s", error)

    async def _refresh_xp_shop_panel(self) -> None:
        channel_id = self._settings.xp_shop_panel_channel_id
        message_id = self._settings.xp_shop_panel_message_id
        if channel_id is None or message_id is None or self.user is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            shop = await self._level_bot_xp.fetch_xp_shop(channel.guild.id, self.user.id)
            if shop is None:
                raise RuntimeError("level-botのXP交換APIへ接続できません")
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=minecraft_xp_shop_embed(shop.packs),
                view=MinecraftXpShopPanelView(self),
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not refresh Minecraft XP shop panel: %s", error)

    async def _refresh_resource_shop_panel(self) -> None:
        channel_id = self._settings.resource_shop_panel_channel_id
        message_id = self._settings.resource_shop_panel_message_id
        if channel_id is None or message_id is None or self.user is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            shop = await self._level_bot_xp.fetch_resource_shop(channel.guild.id, self.user.id)
            if shop is None:
                raise RuntimeError("level-botの資源交換APIへ接続できません")
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=minecraft_resource_shop_embed(shop.packs),
                view=MinecraftResourceShopPanelView(self),
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not refresh Minecraft resource shop panel: %s", error)

    async def _refresh_item_gacha_panel(self) -> None:
        channel_id = self._settings.item_gacha_panel_channel_id
        message_id = self._settings.item_gacha_panel_message_id
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id, require_embeds=True)
            message = await channel.fetch_message(message_id)
            await message.edit(
                embed=item_gacha_panel_embed(),
                view=MinecraftItemGachaPanelView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not refresh Minecraft item gacha panel: %s", error)

    async def _disable_old_panel(self, channel_id: int | None, message_id: int | None) -> None:
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(channel_id)
            message = await channel.fetch_message(message_id)
            await message.edit(
                content="このパネルは移動しました。最新のパネルをご利用ください。",
                embed=None,
                view=None,
            )
        except RuntimeError, discord.DiscordException:
            LOGGER.info("Could not disable old panel message %d", message_id)

    async def _save_settings(self, settings: RuntimeSettings) -> None:
        async with self._settings_lock:
            await asyncio.to_thread(self._settings_store.save, settings)
            self._settings = settings

    def _ensure_same_guild(self, guild_id: int) -> None:
        configured = self._settings.guild_id
        if configured is not None and configured != guild_id:
            raise RuntimeError("別のDiscordサーバーには設定できません")

    async def _require_server_manager(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True
            )
            return False
        if not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "この操作には「サーバーの管理」権限が必要です。", ephemeral=True
            )
            return False
        return True

    async def _ensure_tailer_started(self) -> None:
        if self._tailer_task is not None and not self._tailer_task.done():
            return
        await asyncio.to_thread(self._tailer.validate)
        self._delivery_healthy = True
        self._tailer_task = asyncio.create_task(
            self._forward_logs(integration_only=self._channel is None),
            name="minecraft-log-tailer",
        )
        self._tailer_task.add_done_callback(self._tailer_stopped)

    async def _forward_logs(self, *, integration_only: bool = False) -> None:
        async for pending_line in self._tailer.lines():
            try:
                quest_state = parse_quest_state(pending_line.text)
            except ValueError as error:
                LOGGER.warning("Ignored malformed Minecraft quest state: %s", error)
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if quest_state is not None:
                processed = await self._retry_integration_log_operation(
                    description=(
                        f"quest={quest_state.quest_id} transition={quest_state.transition_id}"
                    ),
                    operation=lambda event=quest_state: self._handle_quest_state(event),
                )
                if processed:
                    await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            try:
                market_listing = parse_market_listing(pending_line.text)
                market_request = (
                    None if market_listing is not None else parse_market_request(pending_line.text)
                )
            except ValueError as error:
                LOGGER.warning("Ignored malformed Minecraft market event: %s", error)
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if market_listing is not None:
                processed = await self._retry_integration_log_operation(
                    description=(
                        f"listing={market_listing.listing_id} "
                        f"seller_uuid={market_listing.seller_uuid}"
                    ),
                    operation=lambda event=market_listing: self._handle_market_listing(event),
                )
                if processed:
                    await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if market_request is not None:
                processed = await self._retry_integration_log_operation(
                    description=(
                        f"request={market_request.request_id} "
                        f"player_uuid={market_request.player_uuid}"
                    ),
                    operation=lambda request=market_request: self._handle_market_request(request),
                )
                if processed:
                    await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            try:
                exchange_request = parse_exchange_request(pending_line.text)
            except ValueError as error:
                LOGGER.warning("Ignored malformed Minecraft exchange request: %s", error)
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if exchange_request is not None:
                if exchange_request.kind == "material_buyback":
                    processed = await self._retry_integration_log_operation(
                        description=(
                            f"material-buyback={exchange_request.request_id} "
                            f"player_uuid={exchange_request.player_uuid}"
                        ),
                        operation=lambda request=exchange_request: (
                            self._handle_minecraft_exchange_request(request)
                        ),
                    )
                    if processed:
                        await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                    continue
                try:
                    await self._handle_minecraft_exchange_request(exchange_request)
                except Exception:
                    LOGGER.exception(
                        "Minecraft exchange request failed request=%s player_uuid=%s",
                        exchange_request.request_id,
                        exchange_request.player_uuid,
                    )
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            try:
                item_gacha_request = parse_item_gacha_request(pending_line.text)
            except ValueError as error:
                LOGGER.warning("Ignored malformed Minecraft item gacha request: %s", error)
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if item_gacha_request is not None:
                try:
                    await self._handle_minecraft_item_gacha_request(item_gacha_request)
                except Exception:
                    LOGGER.exception(
                        "Minecraft item gacha request failed request=%s player_uuid=%s",
                        item_gacha_request.request_id,
                        item_gacha_request.player_uuid,
                    )
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            try:
                emerald_exchange = parse_emerald_diamond_exchange_event(pending_line.text)
            except ValueError as error:
                LOGGER.warning("Ignored malformed Minecraft emerald exchange event: %s", error)
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if emerald_exchange is not None:
                if integration_only and self._channel is None:
                    await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                    continue
                try:
                    account = await asyncio.to_thread(
                        self._accounts.get_by_player_uuid,
                        emerald_exchange.player_uuid,
                    )
                except ValueError as error:
                    LOGGER.warning(
                        "Could not identify emerald exchange account by UUID %s: %s",
                        emerald_exchange.player_uuid,
                        error,
                    )
                    account = None
                guild = self.get_guild(self._settings.guild_id or 0)
                server_name = guild.name if guild is not None else "サーバー"
                embed = format_emerald_diamond_exchange(
                    server_name=server_name,
                    player_name=emerald_exchange.player_name,
                    discord_user_id=(
                        account.discord_user_id
                        if account is not None and account.status == "active"
                        else None
                    ),
                    emerald_count=emerald_exchange.emerald_count,
                    diamond_count=emerald_exchange.diamond_count,
                )
                retry_delay = 1
                while not self.is_closed():
                    await self.wait_until_ready()
                    try:
                        await self._send(embed)
                    except (RuntimeError, discord.DiscordException) as error:
                        self._delivery_healthy = False
                        LOGGER.warning(
                            "Discord emerald exchange log failed; retrying in %ds: %s",
                            retry_delay,
                            error,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 30)
                        continue
                    await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                    self._delivery_healthy = True
                    break
                continue
            try:
                activity_event = parse_activity_event(pending_line.text)
            except ValueError as error:
                LOGGER.warning("Ignored malformed Minecraft activity event: %s", error)
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if activity_event is not None:
                recorded = await self._record_activity_event(activity_event)
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                if recorded:
                    try:
                        await self._deliver_pending_activity_events()
                    except (OSError, RconError, RuntimeError, ValueError) as error:
                        LOGGER.warning(
                            "Minecraft activity event was recorded but delivery failed: %s",
                            error,
                        )
                continue
            event = parse_log_line(pending_line.text)
            if event is None:
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            if event.type is EventType.JOIN:
                self._online_player_names.add(event.player_name.casefold())
            elif event.type is EventType.LEAVE:
                self._online_player_names.discard(event.player_name.casefold())
            if integration_only and self._channel is None:
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            account = await self._find_account_for_player_name(event.player_name)
            discord_user_id, discord_username = await self._discord_identity(account)
            if (
                event.type is EventType.LEAVE
                and account is not None
                and account.discord_user_id is not None
            ):
                await self._send_voice_bonus_final_heartbeat(account)
            embed = format_event(event, self._translator, discord_user_id)
            reward_embed: discord.Embed | None = None
            reward_command: str | None = None
            reward_event = None
            guild_id = self._settings.guild_id
            if (
                event.type is EventType.ADVANCEMENT
                and self._config.minecraft_bonuses_enabled
                and account is not None
                and discord_user_id is not None
                and guild_id is not None
                and self._config.level_bot_api_url
                and self._config.level_bot_api_token
            ):
                advancement = self._translator.translate(event.detail)
                guild = self.get_guild(guild_id)
                server_name = guild.name if guild is not None else "サーバー"
                event_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "mc-bot:advancement:"
                        f"{pending_line.cursor.file_identity}:"
                        f"{pending_line.cursor.offset}:{account.id}:{event.detail}",
                    )
                )
                reward_event = await asyncio.to_thread(
                    self._accounts.claim_advancement_reward,
                    event_id=event_id,
                    account_id=account.id,
                    advancement=event.detail,
                    discord_user_id=discord_user_id,
                    guild_id=guild_id,
                    minecraft_xp=ADVANCEMENT_REWARD_LEVEL_BOT_SOURCE_XP,
                    observed_at=datetime.now(UTC).isoformat(),
                )
                if reward_event is not None:
                    reward_embed = format_advancement_reward(
                        event,
                        advancement,
                        server_name,
                        discord_user_id,
                        minecraft_reward_xp=(
                            ADVANCEMENT_REWARD_IN_GAME_XP if self._rcon is not None else None
                        ),
                    )
                    if self._rcon is not None:
                        reward_command = advancement_reward_tellraw_command(
                            server_name, event.player_name, advancement
                        )
            if event.type in {EventType.JOIN, EventType.LEAVE}:
                self._schedule_player_count_refresh()
                self._schedule_status_panel_refresh()
            retry_delay = 1
            minecraft_reward_sent = False
            event_log_sent = False
            reward_log_sent = False
            while not self.is_closed():
                await self.wait_until_ready()
                if reward_command is not None and not minecraft_reward_sent:
                    try:
                        if reward_event is not None and account is not None:
                            await self._grant_advancement_minecraft_reward(
                                account,
                                event_id=reward_event.event_id,
                                observed_at=reward_event.observed_at,
                            )
                        await asyncio.to_thread(self._require_rcon().execute, reward_command)
                    except (OSError, RconError, RuntimeError, ValueError) as error:
                        self._delivery_healthy = False
                        LOGGER.warning(
                            "Minecraft advancement reward send failed; retrying in %ds: %s",
                            retry_delay,
                            error,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 30)
                        continue
                    minecraft_reward_sent = True
                if reward_event is not None and not await self._deliver_minecraft_xp_outbox():
                    self._delivery_healthy = False
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
                    continue
                try:
                    if not event_log_sent:
                        await self._send(embed)
                        event_log_sent = True
                    if reward_embed is not None and not reward_log_sent:
                        await self._send(reward_embed)
                        reward_log_sent = True
                except (RuntimeError, discord.DiscordException) as error:
                    self._delivery_healthy = False
                    LOGGER.warning(
                        "Discord message send failed; retrying in %ds: %s",
                        retry_delay,
                        error,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
                    continue
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                self._delivery_healthy = True
                self._queue_voice_event(event, discord_username)
                if (
                    account is not None
                    and account.discord_user_id is not None
                    and event.type is EventType.JOIN
                ):
                    # Paper再起動でプラグイン内の状態が空になっていても、再参加時に
                    # VC倍率を必ず再送する。
                    self._voice_bonus_active_users.discard(account.discord_user_id)
                    await self._sync_voice_bonus_for_account(
                        account,
                        announce_standard_xp=True,
                    )
                break

    async def _record_activity_event(self, event: MinecraftActivityEvent) -> bool:
        if not self._config.minecraft_bonuses_enabled:
            return False
        guild_id = self._settings.guild_id
        if (
            guild_id is None
            or not self._config.level_bot_api_url
            or not self._config.level_bot_api_token
        ):
            LOGGER.warning(
                "Ignored Minecraft %s event because level-bot integration is not configured",
                event.kind,
            )
            return False
        account = await asyncio.to_thread(
            self._accounts.get_by_player_uuid,
            event.player_uuid,
        )
        if (
            account is None
            or account.discord_user_id is None
            or account.status not in {"active", "pending_remove"}
        ):
            LOGGER.warning(
                "Ignored Minecraft %s event for unlinked UUID %s (%s)",
                event.kind,
                event.player_uuid,
                event.player_name,
            )
            return False
        if event.kind is ActivityKind.FISHING:
            reward = await asyncio.to_thread(
                self._accounts.record_fishing_catch,
                event_id=event.event_id,
                account_id=account.id,
                discord_user_id=account.discord_user_id,
                guild_id=guild_id,
                observed_at=event.occurred_at,
                combo_window_seconds=FISHING_COMBO_WINDOW_SECONDS,
            )
        elif event.kind is ActivityKind.WOODCUTTING:
            reward = await asyncio.to_thread(
                self._accounts.record_woodcutting_log,
                event_id=event.event_id,
                account_id=account.id,
                discord_user_id=account.discord_user_id,
                guild_id=guild_id,
                observed_at=event.occurred_at,
                combo_window_seconds=WOODCUTTING_COMBO_WINDOW_SECONDS,
            )
        else:
            reward = await asyncio.to_thread(
                self._accounts.record_minecraft_xp_gain,
                event_id=event.event_id,
                account_id=account.id,
                discord_user_id=account.discord_user_id,
                guild_id=guild_id,
                minecraft_xp=event.amount,
                observed_at=event.occurred_at,
            )
        return reward is not None or event.kind is ActivityKind.WOODCUTTING

    def _queue_voice_event(self, event: LogEvent, discord_username: str | None) -> None:
        if not self._settings.voice_enabled or self._settings.guild_id is None:
            return
        text = event_speech_text(
            event,
            self._translator,
            self._config.floodgate_username_prefix,
            discord_username,
        )
        self._voice_player.enqueue(self._settings.guild_id, text)

    async def _find_account_for_player_name(self, player_name: str) -> MinecraftAccount | None:
        try:
            cached_profile = await asyncio.to_thread(
                read_cached_player_profile,
                self._config.minecraft_whitelist_path.with_name("usercache.json"),
                player_name,
            )
            if cached_profile is None:
                legacy_account = await asyncio.to_thread(
                    self._accounts.find_by_player_name, player_name
                )
                return (
                    legacy_account
                    if legacy_account is not None and legacy_account.player_uuid is None
                    else None
                )
            cached_name, player_uuid = cached_profile
            account = await asyncio.to_thread(self._accounts.get_by_player_uuid, player_uuid)
            if account is None:
                legacy_account = await asyncio.to_thread(
                    self._accounts.find_by_player_name, player_name
                )
                if legacy_account is None or legacy_account.player_uuid is not None:
                    return None
                account = legacy_account
            if account is None or account.status not in {"active", "pending_remove"}:
                return None
            minecraft_name = (
                cached_name.removeprefix(self._config.floodgate_username_prefix)
                if account.edition == "bedrock"
                else cached_name
            )
            if (
                account.minecraft_name == minecraft_name
                and account.server_player_name == cached_name
                and account.player_uuid is not None
                and account.player_uuid.casefold() == player_uuid.casefold()
            ):
                return account
            return await asyncio.to_thread(
                self._accounts.update_player_profile,
                account.id,
                minecraft_name=minecraft_name,
                server_player_name=cached_name,
                player_uuid=player_uuid,
            )
        except ValueError as error:
            LOGGER.warning("Could not match Minecraft player %s by UUID: %s", player_name, error)
            return None

    async def _linked_accounts_by_online_name(
        self, player_names: list[str]
    ) -> dict[str, MinecraftAccount]:
        linked: dict[str, MinecraftAccount] = {}
        for player_name in player_names:
            account = await self._find_account_for_player_name(player_name)
            if (
                account is not None
                and account.status == "active"
                and account.discord_user_id is not None
            ):
                linked[player_name.casefold()] = account
        return linked

    async def _discord_identity(
        self, account: MinecraftAccount | None
    ) -> tuple[int | None, str | None]:
        if account is None or account.discord_user_id is None:
            return None, None
        guild = self.get_guild(self._settings.guild_id or 0)
        member = guild.get_member(account.discord_user_id) if guild is not None else None
        if member is not None and member.display_name != account.discord_username:
            await asyncio.to_thread(
                self._accounts.update_discord_username,
                member.id,
                member.display_name,
            )
        username = member.display_name if member is not None else account.discord_username
        return account.discord_user_id, username

    async def _send(self, embed: discord.Embed) -> None:
        if self._channel is None:
            raise RuntimeError("Discord channel has not been validated")
        await self._channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _flush_minecraft_item_gacha_notifications(self, guild_id: int) -> None:
        async with self._item_gacha_notification_lock:
            await self._flush_minecraft_item_gacha_notifications_locked(guild_id)

    async def _recover_minecraft_item_gacha_notifications(self, guild_id: int) -> None:
        pending = await asyncio.to_thread(
            self._accounts.has_pending_minecraft_item_gacha_notifications,
            guild_id=guild_id,
        )
        if pending:
            await self._flush_minecraft_item_gacha_notifications(guild_id)

    async def _flush_minecraft_item_gacha_notifications_locked(self, guild_id: int) -> None:
        draws = await asyncio.to_thread(
            self._accounts.list_pending_minecraft_item_gacha_notifications
        )
        for draw in draws:
            if draw.guild_id != guild_id:
                continue
            if not self._item_gacha_draw_matches_catalog(draw):
                LOGGER.error(
                    "Could not notify changed Minecraft item gacha reward draw=%s",
                    draw.draw_id,
                )
                await asyncio.to_thread(
                    self._accounts.begin_minecraft_item_gacha_notification_attempt,
                    draw.draw_id,
                    "minecraft",
                )
                await asyncio.to_thread(
                    self._accounts.begin_minecraft_item_gacha_notification_attempt,
                    draw.draw_id,
                    "discord",
                )
                continue
            if not draw.minecraft_notified:
                attempt = await asyncio.to_thread(
                    self._accounts.begin_minecraft_item_gacha_notification_attempt,
                    draw.draw_id,
                    "minecraft",
                )
                if attempt is not None:
                    try:
                        await self._execute_checked_rcon(
                            item_gacha_tellraw_command(
                                draw.player_name,
                                draw.reward_key,
                                cast(ItemGachaCategory, draw.draw_category),
                            )
                        )
                    except (OSError, RconError, RuntimeError, ValueError) as error:
                        LOGGER.warning(
                            "Could not announce item gacha draw in Minecraft draw=%s: %s",
                            draw.draw_id,
                            error,
                        )
                        if attempt >= ITEM_GACHA_NOTIFICATION_RETRY_LIMIT:
                            LOGGER.error(
                                "Giving up Minecraft item gacha notification after %s attempts "
                                "draw=%s",
                                attempt,
                                draw.draw_id,
                            )
                    else:
                        await asyncio.to_thread(
                            self._accounts.mark_minecraft_item_gacha_notified,
                            draw.draw_id,
                            "minecraft",
                        )
            if not draw.discord_notified:
                attempt = await asyncio.to_thread(
                    self._accounts.begin_minecraft_item_gacha_notification_attempt,
                    draw.draw_id,
                    "discord",
                )
                if attempt is None:
                    continue
                try:
                    await self._send_item_gacha_log(draw)
                except (RuntimeError, discord.DiscordException) as error:
                    LOGGER.warning(
                        "Could not announce item gacha draw in Discord draw=%s: %s",
                        draw.draw_id,
                        error,
                    )
                    if attempt >= ITEM_GACHA_NOTIFICATION_RETRY_LIMIT:
                        LOGGER.error(
                            "Giving up Discord item gacha notification after %s attempts draw=%s",
                            attempt,
                            draw.draw_id,
                        )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_item_gacha_notified,
                        draw.draw_id,
                        "discord",
                    )

    async def _send_item_gacha_log(self, draw: MinecraftItemGachaDraw) -> None:
        if self._channel is None:
            raise RuntimeError("Discord channel has not been validated")
        await self._channel.send(
            content=f"<@{draw.discord_user_id}>",
            embed=item_gacha_result_embed(
                player_name=draw.player_name,
                discord_user_id=draw.discord_user_id,
                reward_key=draw.reward_key,
                category=cast(ItemGachaCategory, draw.draw_category),
            ),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=[discord.Object(id=draw.discord_user_id)],
                roles=False,
                replied_user=False,
            ),
        )

    async def _resolve_and_validate_channel(
        self,
        channel_id: int,
        *,
        require_embeds: bool = False,
        require_message_history: bool = False,
    ) -> discord.TextChannel:
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"Discord channel {channel_id} is not a text channel")
        member = channel.guild.me
        if member is None:
            raise RuntimeError("The bot is not a member of the configured Discord server")
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.send_messages:
            raise RuntimeError("Botに「チャンネルを見る」「メッセージを送信」権限が必要です")
        if require_embeds and not permissions.embed_links:
            raise RuntimeError("Botに「埋め込みリンク」権限が必要です")
        if require_message_history and not permissions.read_message_history:
            raise RuntimeError("Botに「メッセージ履歴を読む」権限が必要です")
        return channel

    async def _connect_voice_channel(self, channel: discord.VoiceChannel) -> bool:
        member = channel.guild.me
        if member is None:
            raise RuntimeError("BotがDiscordサーバーに参加していません")
        permissions = channel.permissions_for(member)
        if not permissions.connect or not permissions.speak:
            raise RuntimeError("BotにVCの「接続」と「発言」権限が必要です")
        voice_client = channel.guild.voice_client
        if voice_client is None or not voice_client.is_connected():
            if voice_client is not None:
                await voice_client.disconnect(force=True)
            await channel.connect(timeout=15, reconnect=True, self_deaf=True)
            return True
        if voice_client.channel.id != channel.id:
            await voice_client.move_to(channel)
            return True
        return False

    async def _restore_voice_connection(self) -> None:
        channel_id = self._settings.voice_channel_id
        if channel_id is None:
            return
        if not self._voice_player.configured:
            LOGGER.warning("Minecraft voice is enabled but VOICEVOX TTS API is not configured")
            return
        try:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                raise RuntimeError("設定済みの読み上げ先がVCではありません")
            await self._connect_voice_channel(channel)
        except (OSError, RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not restore Minecraft voice connection: %s", error)

    async def _health_loop(self) -> None:
        while not self.is_closed():
            unconfigured = self._channel is None
            tailer_running = self._tailer_task is not None and not self._tailer_task.done()
            forwarding_healthy = tailer_running and self._delivery_healthy
            if self.is_ready() and (unconfigured or forwarding_healthy):
                self._health_path.touch()
            else:
                self._remove_health_file()
            await self._resume_whitelist_if_due()
            self._sync_ticks += 1
            if self._sync_ticks >= 6:
                self._sync_ticks = 0
                await self._sync_whitelist_accounts()
                if self._settings.voice_enabled:
                    await self._restore_voice_connection()
                if self._settings.guild_id is not None:
                    try:
                        await self._recover_minecraft_item_gacha_notifications(
                            self._settings.guild_id
                        )
                    except (OSError, RuntimeError, ValueError) as error:
                        LOGGER.warning("Could not flush item gacha notifications: %s", error)
            self._schedule_player_count_refresh(delay=0)
            self._schedule_periodic_status_panel_refresh()
            self._ensure_minecraft_xp_started()
            self._ensure_activity_delivery_started()
            await asyncio.sleep(10)

    def _ensure_minecraft_xp_started(self) -> None:
        if (
            not self._config.level_bot_api_url
            or not self._config.level_bot_api_token
            or self._rcon is None
        ):
            return
        if self._minecraft_xp_task is not None and not self._minecraft_xp_task.done():
            return
        self._minecraft_xp_task = asyncio.create_task(
            self._minecraft_xp_loop(), name="minecraft-xp-sync"
        )

    def _ensure_activity_delivery_started(self) -> None:
        if (
            not self._config.minecraft_bonuses_enabled
            or not self._config.level_bot_api_url
            or not self._config.level_bot_api_token
            or self._rcon is None
        ):
            return
        if self._activity_delivery_task is not None and not self._activity_delivery_task.done():
            return
        self._activity_delivery_task = asyncio.create_task(
            self._activity_delivery_loop(), name="minecraft-activity-delivery"
        )

    async def _activity_delivery_loop(self) -> None:
        while not self.is_closed():
            try:
                await self._deliver_pending_activity_events()
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning("Minecraft activity reward delivery failed: %s", error)
            await asyncio.sleep(30)

    async def _deliver_pending_activity_events(self) -> None:
        async with self._activity_delivery_lock:
            await self._deliver_pending_activity_events_locked()

    async def _deliver_pending_activity_events_locked(self) -> None:
        await self._deliver_minecraft_xp_outbox()
        fishing = await asyncio.to_thread(self._accounts.list_pending_fishing_reward_deliveries)
        for reward in fishing:
            account = await asyncio.to_thread(self._accounts.get, reward.account_id)
            if account is None:
                LOGGER.warning(
                    "Could not deliver fishing reward for missing account event=%s",
                    reward.event_id,
                )
                continue
            try:
                await self._grant_fishing_combo_reward(account, reward)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Could not deliver fishing reward event=%s: %s",
                    reward.event_id,
                    error,
                )
        await self._deliver_fishing_public_announcements()
        await self._deliver_fishing_combo_audits()

        woodcutting = await asyncio.to_thread(
            self._accounts.list_pending_woodcutting_reward_deliveries
        )
        for reward in woodcutting:
            account = await asyncio.to_thread(self._accounts.get, reward.account_id)
            if account is None:
                LOGGER.warning(
                    "Could not deliver woodcutting reward for missing account event=%s",
                    reward.event_id,
                )
                continue
            try:
                await self._grant_woodcutting_combo_reward(account, reward)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Could not deliver woodcutting reward event=%s: %s",
                    reward.event_id,
                    error,
                )
        await self._deliver_woodcutting_public_announcements()
        await self._deliver_woodcutting_combo_audits()

    async def _grant_fishing_combo_reward(
        self,
        account: MinecraftAccount,
        reward: FishingComboRewardEvent,
    ) -> None:
        """Minecraft内だけにコンボXPを付与し、通常XP同期から除外する。"""
        async with self._minecraft_xp_observation_lock:
            reserved = await asyncio.to_thread(
                self._accounts.reserve_fishing_reward_delivery,
                event_id=reward.event_id,
                account_id=account.id,
                reward_xp=reward.reward_xp,
                observed_at=reward.observed_at,
            )
            if not reserved:
                return
            try:
                await self._execute_checked_rcon(
                    experience_add_points_command(
                        account.server_player_name,
                        reward.reward_xp,
                    )
                )
            except ValueError:
                await asyncio.to_thread(
                    self._accounts.release_fishing_reward_delivery,
                    event_id=reward.event_id,
                    account_id=account.id,
                    reward_xp=reward.reward_xp,
                )
                raise

        if is_public_fishing_milestone(reward.combo_count):
            await self._clear_combo_actionbar(account, reward.event_id, "fishing")
        else:
            try:
                await self._execute_checked_rcon(
                    fishing_combo_actionbar_command(
                        account.server_player_name,
                        reward.combo_count,
                        reward.reward_xp,
                    )
                )
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Could not notify fishing combo player=%s event=%s: %s",
                    account.server_player_name,
                    reward.event_id,
                    error,
                )

    async def _deliver_fishing_public_announcements(self) -> None:
        events = await asyncio.to_thread(self._accounts.list_pending_fishing_public_deliveries)
        for event in events:
            account = await asyncio.to_thread(self._accounts.get, event.account_id)
            if account is None:
                LOGGER.warning(
                    "Could not deliver fishing milestone for missing account event=%s",
                    event.event_id,
                )
                continue
            if not event.minecraft_public_delivered:
                try:
                    await self._execute_checked_rcon(
                        fishing_combo_tellraw_command(
                            account.server_player_name,
                            event.combo_count,
                            event.reward_xp,
                        )
                    )
                except (OSError, RconError, RuntimeError, ValueError) as error:
                    LOGGER.warning(
                        "Could not announce fishing milestone in Minecraft player=%s event=%s: %s",
                        account.server_player_name,
                        event.event_id,
                        error,
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_fishing_public_delivered,
                        event.event_id,
                        "minecraft",
                    )
            if not event.discord_public_delivered:
                try:
                    await self._send(
                        format_fishing_combo_milestone(
                            player_name=account.server_player_name,
                            discord_user_id=event.discord_user_id,
                            combo_count=event.combo_count,
                            reward_xp=event.reward_xp,
                        )
                    )
                except (RuntimeError, discord.DiscordException) as error:
                    LOGGER.warning(
                        "Could not announce fishing milestone in Discord player=%s event=%s: %s",
                        account.server_player_name,
                        event.event_id,
                        error,
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_fishing_public_delivered,
                        event.event_id,
                        "discord",
                    )

    async def _clear_combo_actionbar(
        self,
        account: MinecraftAccount,
        event_id: str,
        combo_kind: str,
    ) -> None:
        try:
            await self._execute_checked_rcon(actionbar_clear_command(account.server_player_name))
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning(
                "Could not clear %s combo actionbar player=%s event=%s: %s",
                combo_kind,
                account.server_player_name,
                event_id,
                error,
            )

    async def _deliver_fishing_combo_audits(self) -> bool:
        events = await asyncio.to_thread(self._accounts.list_pending_fishing_audits)
        for event in events:
            if not await self._level_bot_xp.send_fishing_combo(event):
                return False
            await asyncio.to_thread(
                self._accounts.mark_fishing_audit_delivered,
                event.event_id,
            )
        return True

    async def _grant_woodcutting_combo_reward(
        self,
        account: MinecraftAccount,
        reward: WoodcuttingComboRewardEvent,
    ) -> None:
        """Minecraft内だけに木こりXPを付与し、通常XP同期から除外する。"""
        async with self._minecraft_xp_observation_lock:
            reserved = await asyncio.to_thread(
                self._accounts.reserve_woodcutting_reward_delivery,
                event_id=reward.event_id,
                account_id=account.id,
                reward_xp=reward.reward_xp,
                observed_at=reward.observed_at,
            )
            if not reserved:
                return
            try:
                await self._execute_checked_rcon(
                    experience_add_points_command(account.server_player_name, reward.reward_xp)
                )
            except ValueError:
                await asyncio.to_thread(
                    self._accounts.release_woodcutting_reward_delivery,
                    event_id=reward.event_id,
                    account_id=account.id,
                    reward_xp=reward.reward_xp,
                )
                raise

        if is_public_woodcutting_milestone(reward.combo_count):
            await self._clear_combo_actionbar(account, reward.event_id, "woodcutting")
        else:
            try:
                await self._execute_checked_rcon(
                    woodcutting_actionbar_command(
                        account.server_player_name,
                        reward.combo_count,
                        reward.reward_xp,
                    )
                )
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Could not notify woodcutting combo player=%s event=%s: %s",
                    account.server_player_name,
                    reward.event_id,
                    error,
                )
        try:
            await self._execute_checked_rcon(
                woodcutting_xp_sound_command(account.server_player_name)
            )
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning(
                "Could not play woodcutting XP sound player=%s event=%s: %s",
                account.server_player_name,
                reward.event_id,
                error,
            )

    async def _deliver_woodcutting_public_announcements(self) -> None:
        events = await asyncio.to_thread(self._accounts.list_pending_woodcutting_public_deliveries)
        for event in events:
            account = await asyncio.to_thread(self._accounts.get, event.account_id)
            if account is None:
                LOGGER.warning(
                    "Could not deliver woodcutting milestone for missing account event=%s",
                    event.event_id,
                )
                continue
            if not event.minecraft_public_delivered:
                try:
                    await self._execute_checked_rcon(
                        woodcutting_tellraw_command(
                            account.server_player_name,
                            event.combo_count,
                            event.reward_xp,
                        )
                    )
                except (OSError, RconError, RuntimeError, ValueError) as error:
                    LOGGER.warning(
                        "Could not announce woodcutting milestone in Minecraft "
                        "player=%s event=%s: %s",
                        account.server_player_name,
                        event.event_id,
                        error,
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_woodcutting_public_delivered,
                        event.event_id,
                        "minecraft",
                    )
            if not event.discord_public_delivered:
                try:
                    await self._send(
                        format_woodcutting_combo_milestone(
                            player_name=account.server_player_name,
                            discord_user_id=event.discord_user_id,
                            combo_count=event.combo_count,
                            reward_xp=event.reward_xp,
                        )
                    )
                except (RuntimeError, discord.DiscordException) as error:
                    LOGGER.warning(
                        "Could not announce woodcutting milestone in Discord "
                        "player=%s event=%s: %s",
                        account.server_player_name,
                        event.event_id,
                        error,
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_woodcutting_public_delivered,
                        event.event_id,
                        "discord",
                    )

    async def _deliver_woodcutting_combo_audits(self) -> bool:
        events = await asyncio.to_thread(self._accounts.list_pending_woodcutting_audits)
        for event in events:
            if not await self._level_bot_xp.send_woodcutting_combo(event):
                return False
            await asyncio.to_thread(
                self._accounts.mark_woodcutting_audit_delivered,
                event.event_id,
            )
        return True

    async def _minecraft_xp_loop(self) -> None:
        while not self.is_closed():
            try:
                await self._sync_minecraft_level_up_announcements()
                await self._sync_minecraft_xp()
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning("Minecraft XP synchronization failed: %s", error)
            await asyncio.sleep(self._config.minecraft_integration_sync_seconds)

    async def _sync_minecraft_level_up_announcements(self) -> None:
        guild_id = self._settings.guild_id
        if guild_id is None:
            return
        events = await self._level_bot_xp.fetch_level_ups(guild_id)
        if events is None:
            return
        online_user_ids: set[int] = set()
        if any(not event.minecraft_delivered or not event.discord_delivered for event in events):
            linked = await self._linked_accounts_by_online_name(list(self._online_player_names))
            online_user_ids = {
                account.discord_user_id
                for account in linked.values()
                if account.discord_user_id is not None
            }
        for event in events:
            if event.guild_id != guild_id:
                LOGGER.warning(
                    "Ignored level-up event for another guild event=%d guild=%d",
                    event.id,
                    event.guild_id,
                )
                continue
            if not event.minecraft_delivered:
                if event.user_id in online_user_ids:
                    command = level_up_tellraw_command(event)
                    await asyncio.to_thread(self._require_rcon().execute, command)
                if not await self._level_bot_xp.acknowledge_level_up(
                    event.id, guild_id, "minecraft"
                ):
                    # ACK失敗時は次回再送する。通知欠落より稀な重複を優先する。
                    return
            if not event.discord_delivered:
                if event.user_id in online_user_ids:
                    await self._send(format_level_up_event(event))
                if not await self._level_bot_xp.acknowledge_level_up(event.id, guild_id, "discord"):
                    return

    async def _sync_minecraft_xp(self) -> None:
        guild_id = self._settings.guild_id
        if guild_id is None:
            return
        await self._deliver_minecraft_xp_outbox()

        online = list(self._online_player_names)
        linked = await self._linked_accounts_by_online_name(online)
        await self._sync_minecraft_xp_exchanges(
            guild_id=guild_id,
            online_names=self._online_player_names,
            linked_accounts=tuple(linked.values()),
        )
        await self._sync_minecraft_resource_exchanges(
            guild_id=guild_id,
            online_names=self._online_player_names,
            linked_accounts=tuple(linked.values()),
        )
        observed_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        await self._sync_voice_bonus_heartbeats(
            online,
            linked=linked,
            guild_id=guild_id,
            observed_at=observed_at,
        )
        await self._deliver_minecraft_xp_outbox()

    async def _sync_minecraft_xp_exchanges(
        self,
        *,
        guild_id: int,
        online_names: set[str],
        linked_accounts: tuple[MinecraftAccount, ...],
    ) -> None:
        await self._flush_minecraft_xp_exchange_deliveries(guild_id)
        events = await self._level_bot_xp.fetch_xp_exchanges(guild_id)
        if events is None:
            return
        accounts_by_external_id = {f"mc-bot:{account.id}": account for account in linked_accounts}
        for event in events:
            if event.guild_id != guild_id:
                continue
            claim_token = await asyncio.to_thread(
                self._accounts.get_minecraft_xp_exchange_claim_token,
                event.event_id,
            )
            if event.status == "delivering" and (
                claim_token is None
                or not await self._level_bot_xp.update_xp_exchange(
                    event.id,
                    guild_id,
                    "claim",
                    claim_token=claim_token,
                )
            ):
                continue

            existing_delivery = await asyncio.to_thread(
                self._accounts.get_minecraft_xp_exchange_delivery,
                event.event_id,
            )
            if existing_delivery is not None:
                if not existing_delivery.reward_applied:
                    LOGGER.error(
                        "XP exchange requires manual delivery audit event=%d",
                        event.id,
                    )
                continue

            account = accounts_by_external_id.get(event.minecraft_account_id)
            if (
                account is None
                or account.discord_user_id != event.user_id
                or account.server_player_name.casefold() not in online_names
            ):
                await self._level_bot_xp.update_xp_exchange(
                    event.id,
                    guild_id,
                    "cancel",
                    claim_token=claim_token,
                )
                continue
            if event.status == "pending":
                claim_token = await asyncio.to_thread(
                    self._accounts.get_or_create_minecraft_xp_exchange_claim_token,
                    event.event_id,
                )
                if not await self._level_bot_xp.update_xp_exchange(
                    event.id,
                    guild_id,
                    "claim",
                    claim_token=claim_token,
                ):
                    continue
            if claim_token is None:
                continue

            observed_at = datetime.now(UTC).replace(microsecond=0).isoformat()
            async with self._minecraft_xp_observation_lock:
                try:
                    current_xp = await self._query_player_experience(account.server_player_name)
                except OSError, RconError, ValueError:
                    await self._level_bot_xp.update_xp_exchange(
                        event.id,
                        guild_id,
                        "cancel",
                        claim_token=claim_token,
                    )
                    continue
                reserved = await asyncio.to_thread(
                    self._accounts.reserve_minecraft_xp_exchange_delivery,
                    exchange_id=event.event_id,
                    level_exchange_id=event.id,
                    account_id=account.id,
                    discord_user_id=event.user_id,
                    guild_id=guild_id,
                    player_name=account.server_player_name,
                    cost_xp=event.cost_xp,
                    reward_xp=event.reward_xp,
                    claim_token=claim_token,
                    current_xp=current_xp,
                    observed_at=observed_at,
                )
                if not reserved:
                    continue
                try:
                    await self._execute_checked_rcon(
                        experience_add_points_command(account.server_player_name, event.reward_xp)
                    )
                except ValueError:
                    await asyncio.to_thread(
                        self._accounts.release_minecraft_xp_exchange_delivery,
                        exchange_id=event.event_id,
                        account_id=account.id,
                        current_xp=current_xp,
                        observed_at=observed_at,
                    )
                    await self._level_bot_xp.update_xp_exchange(
                        event.id,
                        guild_id,
                        "cancel",
                        claim_token=claim_token,
                    )
                    continue
                except (OSError, RconError, RuntimeError) as error:
                    # 実行有無を断定できないため、消費確定も再付与もせず監査待ちにする。
                    LOGGER.warning(
                        "Minecraft XP exchange delivery became ambiguous event=%d: %s",
                        event.id,
                        error,
                    )
                    continue
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_xp_exchange_reward_applied,
                    event.event_id,
                )

        await self._flush_minecraft_xp_exchange_deliveries(guild_id)

    async def _flush_minecraft_xp_exchange_deliveries(self, guild_id: int) -> None:
        deliveries = await asyncio.to_thread(
            self._accounts.list_pending_minecraft_xp_exchange_deliveries
        )
        guild = self.get_guild(guild_id)
        server_name = guild.name if guild is not None else "サーバー"
        for delivery in deliveries:
            if delivery.guild_id != guild_id:
                continue
            if not delivery.level_completed:
                completed = await self._level_bot_xp.update_xp_exchange(
                    delivery.level_exchange_id,
                    guild_id,
                    "complete",
                    claim_token=delivery.claim_token,
                )
                if not completed:
                    continue
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_xp_exchange_level_completed,
                    delivery.exchange_id,
                )
                delivery = await asyncio.to_thread(
                    self._accounts.get_minecraft_xp_exchange_delivery,
                    delivery.exchange_id,
                )
                if delivery is None:
                    continue
            if not delivery.minecraft_notified:
                try:
                    await asyncio.to_thread(
                        self._require_rcon().execute,
                        xp_exchange_tellraw_command(
                            server_name,
                            delivery.player_name,
                            delivery.cost_xp,
                            delivery.reward_xp,
                        ),
                    )
                except (OSError, RconError, RuntimeError) as error:
                    LOGGER.warning("Could not announce XP exchange in Minecraft: %s", error)
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_xp_exchange_notified,
                        delivery.exchange_id,
                        "minecraft",
                    )
            if not delivery.discord_notified:
                try:
                    await self._send(
                        format_xp_exchange(
                            server_name=server_name,
                            player_name=delivery.player_name,
                            discord_user_id=delivery.discord_user_id,
                            cost_xp=delivery.cost_xp,
                            reward_xp=delivery.reward_xp,
                        )
                    )
                except (RuntimeError, discord.DiscordException) as error:
                    LOGGER.warning("Could not announce XP exchange in Discord: %s", error)
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_xp_exchange_notified,
                        delivery.exchange_id,
                        "discord",
                    )

    async def _sync_minecraft_resource_exchanges(
        self,
        *,
        guild_id: int,
        online_names: set[str],
        linked_accounts: tuple[MinecraftAccount, ...],
    ) -> None:
        await self._flush_minecraft_resource_exchange_deliveries(guild_id)
        events = await self._level_bot_xp.fetch_resource_exchanges(guild_id)
        if events is None:
            return
        accounts_by_external_id = {f"mc-bot:{account.id}": account for account in linked_accounts}
        for event in events:
            if event.guild_id != guild_id:
                continue
            claim_token = await asyncio.to_thread(
                self._accounts.get_minecraft_resource_exchange_claim_token,
                event.event_id,
            )
            if event.status == "delivering" and (
                claim_token is None
                or not await self._level_bot_xp.update_resource_exchange(
                    event.id,
                    guild_id,
                    "claim",
                    claim_token=claim_token,
                )
            ):
                continue

            existing_delivery = await asyncio.to_thread(
                self._accounts.get_minecraft_resource_exchange_delivery,
                event.event_id,
            )
            if existing_delivery is not None:
                if not existing_delivery.reward_applied:
                    LOGGER.error(
                        "Resource exchange requires manual delivery audit event=%d",
                        event.id,
                    )
                continue

            account = accounts_by_external_id.get(event.minecraft_account_id)
            if (
                account is None
                or account.discord_user_id != event.user_id
                or account.server_player_name.casefold() not in online_names
            ):
                await self._level_bot_xp.update_resource_exchange(
                    event.id,
                    guild_id,
                    "cancel",
                    claim_token=claim_token,
                )
                continue
            if event.status == "pending":
                claim_token = await asyncio.to_thread(
                    self._accounts.get_or_create_minecraft_resource_exchange_claim_token,
                    event.event_id,
                )
                if not await self._level_bot_xp.update_resource_exchange(
                    event.id,
                    guild_id,
                    "claim",
                    claim_token=claim_token,
                ):
                    continue
            if claim_token is None:
                continue

            reserved = await asyncio.to_thread(
                self._accounts.reserve_minecraft_resource_exchange_delivery,
                exchange_id=event.event_id,
                level_exchange_id=event.id,
                account_id=account.id,
                discord_user_id=event.user_id,
                guild_id=guild_id,
                player_name=account.server_player_name,
                item_id=event.item_id,
                item_name=event.item_name,
                item_count=event.item_count,
                cost_xp=event.cost_xp,
                claim_token=claim_token,
            )
            if not reserved:
                continue
            try:
                await self._execute_checked_rcon(
                    resource_give_command(
                        account.server_player_name,
                        event.item_id,
                        event.item_count,
                    )
                )
            except ValueError:
                await asyncio.to_thread(
                    self._accounts.release_minecraft_resource_exchange_delivery,
                    event.event_id,
                )
                await self._level_bot_xp.update_resource_exchange(
                    event.id,
                    guild_id,
                    "cancel",
                    claim_token=claim_token,
                )
                continue
            except (OSError, RconError, RuntimeError) as error:
                LOGGER.warning(
                    "Minecraft resource exchange delivery became ambiguous event=%d: %s",
                    event.id,
                    error,
                )
                continue
            await asyncio.to_thread(
                self._accounts.mark_minecraft_resource_exchange_reward_applied,
                event.event_id,
            )

        await self._flush_minecraft_resource_exchange_deliveries(guild_id)

    async def _flush_minecraft_resource_exchange_deliveries(self, guild_id: int) -> None:
        deliveries = await asyncio.to_thread(
            self._accounts.list_pending_minecraft_resource_exchange_deliveries
        )
        guild = self.get_guild(guild_id)
        server_name = guild.name if guild is not None else "サーバー"
        for delivery in deliveries:
            if delivery.guild_id != guild_id:
                continue
            if not delivery.level_completed:
                completed = await self._level_bot_xp.update_resource_exchange(
                    delivery.level_exchange_id,
                    guild_id,
                    "complete",
                    claim_token=delivery.claim_token,
                )
                if not completed:
                    continue
                await asyncio.to_thread(
                    self._accounts.mark_minecraft_resource_exchange_level_completed,
                    delivery.exchange_id,
                )
                delivery = await asyncio.to_thread(
                    self._accounts.get_minecraft_resource_exchange_delivery,
                    delivery.exchange_id,
                )
                if delivery is None:
                    continue
            if not delivery.minecraft_notified:
                try:
                    await self._execute_checked_rcon(
                        resource_exchange_actionbar_command(
                            delivery.player_name,
                            delivery.item_id,
                            delivery.item_count,
                            delivery.cost_xp,
                        )
                    )
                except (OSError, RconError, RuntimeError, ValueError) as error:
                    LOGGER.warning(
                        "Could not notify resource exchange recipient in Minecraft: %s",
                        error,
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_resource_exchange_notified,
                        delivery.exchange_id,
                        "recipient",
                    )
            if not delivery.minecraft_public_notified:
                try:
                    await asyncio.to_thread(
                        self._require_rcon().execute,
                        resource_exchange_tellraw_command(
                            server_name,
                            delivery.player_name,
                            delivery.item_id,
                            delivery.item_count,
                            delivery.cost_xp,
                        ),
                    )
                except (OSError, RconError, RuntimeError, ValueError) as error:
                    LOGGER.warning(
                        "Could not announce resource exchange in Minecraft: %s",
                        error,
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_resource_exchange_notified,
                        delivery.exchange_id,
                        "minecraft",
                    )
            if not delivery.discord_notified:
                try:
                    await self._send(
                        format_resource_exchange(
                            server_name=server_name,
                            player_name=delivery.player_name,
                            discord_user_id=delivery.discord_user_id,
                            cost_xp=delivery.cost_xp,
                            item_name=delivery.item_name,
                            item_count=delivery.item_count,
                        )
                    )
                except (RuntimeError, discord.DiscordException) as error:
                    LOGGER.warning(
                        "Could not announce resource exchange in Discord: %s",
                        error,
                    )
                else:
                    await asyncio.to_thread(
                        self._accounts.mark_minecraft_resource_exchange_notified,
                        delivery.exchange_id,
                        "discord",
                    )

    async def _sync_voice_bonus_heartbeats(
        self,
        online: list[str],
        *,
        linked: dict[str, MinecraftAccount],
        guild_id: int,
        observed_at: str,
    ) -> set[int]:
        by_user: dict[int, MinecraftAccount] = {}
        for player_name in online:
            account = linked.get(player_name.casefold())
            if account is not None and account.discord_user_id is not None:
                by_user.setdefault(account.discord_user_id, account)

        if not self._config.minecraft_bonuses_enabled:
            for account in by_user.values():
                await self._set_voice_bonus_state(account, active=False, notify=False)
            self._voice_bonus_active_users.clear()
            return set()

        for user_id in self._voice_bonus_active_users - set(by_user):
            self._voice_bonus_active_users.discard(user_id)
        active_users: set[int] = set()
        for account in by_user.values():
            user_id = account.discord_user_id
            if user_id is None:
                continue
            result = await self._level_bot_xp.send_voice_heartbeat(
                guild_id=guild_id,
                discord_user_id=user_id,
                account_id=account.id,
                observed_at=observed_at,
            )
            if result is not None:
                if result.bonus_active:
                    active_users.add(user_id)
                notify = user_id in self._voice_bonus_initialized_users
                self._voice_bonus_initialized_users.add(user_id)
                await self._set_voice_bonus_state(
                    account,
                    active=result.bonus_active,
                    notify=notify,
                )
        return active_users

    async def _sync_voice_bonus_for_discord_user(self, user_id: int) -> None:
        linked = await self._linked_accounts_by_online_name(list(self._online_player_names))
        account = next(
            (candidate for candidate in linked.values() if candidate.discord_user_id == user_id),
            None,
        )
        if account is None:
            self._voice_bonus_active_users.discard(user_id)
            return
        await self._sync_voice_bonus_for_account(account)

    async def _sync_voice_bonus_for_account(
        self,
        account: MinecraftAccount,
        *,
        announce_standard_xp: bool = False,
    ) -> None:
        guild_id = self._settings.guild_id
        user_id = account.discord_user_id
        if guild_id is None or user_id is None:
            return
        if not self._config.minecraft_bonuses_enabled:
            await self._set_voice_bonus_state(account, active=False, notify=False)
            return
        observed_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        result = await self._level_bot_xp.send_voice_heartbeat(
            guild_id=guild_id,
            discord_user_id=user_id,
            account_id=account.id,
            observed_at=observed_at,
        )
        if result is not None:
            self._voice_bonus_initialized_users.add(user_id)
            await self._set_voice_bonus_state(account, active=result.bonus_active)
            if announce_standard_xp and not result.bonus_active:
                await self._announce_server_xp_started(account)

    async def _send_voice_bonus_final_heartbeat(self, account: MinecraftAccount) -> None:
        guild_id = self._settings.guild_id
        user_id = account.discord_user_id
        if self._config.minecraft_bonuses_enabled and guild_id is not None and user_id is not None:
            await self._level_bot_xp.send_voice_heartbeat(
                guild_id=guild_id,
                discord_user_id=user_id,
                account_id=account.id,
                observed_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            )
        await self._set_voice_bonus_state(account, active=False)

    async def _observe_minecraft_xp_for_account(
        self,
        account: MinecraftAccount,
        *,
        guild_id: int,
        observed_at: str,
        double_in_game_xp: bool,
    ) -> None:
        async with self._minecraft_xp_observation_lock:
            await self._observe_minecraft_xp_for_account_locked(
                account,
                guild_id=guild_id,
                observed_at=observed_at,
                double_in_game_xp=double_in_game_xp,
            )

    async def _grant_advancement_minecraft_reward(
        self,
        account: MinecraftAccount,
        *,
        event_id: str,
        observed_at: str,
    ) -> None:
        """進捗の固定ゲーム内XPを一度だけ付与し、通常の増加観測から除外する。"""
        async with self._minecraft_xp_observation_lock:
            reserved = await asyncio.to_thread(
                self._accounts.reserve_advancement_minecraft_reward_delivery,
                event_id=event_id,
                account_id=account.id,
                reward_xp=ADVANCEMENT_REWARD_IN_GAME_XP,
                observed_at=observed_at,
            )
            if not reserved:
                return
            try:
                await self._execute_checked_rcon(
                    experience_add_points_command(
                        account.server_player_name,
                        ADVANCEMENT_REWARD_IN_GAME_XP,
                    )
                )
            except ValueError:
                # Minecraftが明示的に失敗を返した場合は未付与と断定できるため再試行可能にする。
                await asyncio.to_thread(
                    self._accounts.release_advancement_minecraft_reward_delivery,
                    event_id=event_id,
                    account_id=account.id,
                    reward_xp=ADVANCEMENT_REWARD_IN_GAME_XP,
                )
                raise

    async def _observe_minecraft_xp_for_account_locked(
        self,
        account: MinecraftAccount,
        *,
        guild_id: int,
        observed_at: str,
        double_in_game_xp: bool,
    ) -> None:
        user_id = account.discord_user_id
        if user_id is None:
            return
        try:
            current_xp = await self._query_player_experience(account.server_player_name)
        except (OSError, RconError, ValueError) as error:
            LOGGER.debug("Could not query XP for %s: %s", account.server_player_name, error)
            return
        gained_event = await asyncio.to_thread(
            self._accounts.observe_minecraft_xp,
            account_id=account.id,
            discord_user_id=user_id,
            guild_id=guild_id,
            current_xp=current_xp,
            observed_at=observed_at,
            double_in_game_xp=double_in_game_xp,
        )
        if gained_event is None or not double_in_game_xp:
            return
        command = experience_add_points_command(
            account.server_player_name, gained_event.minecraft_xp
        )
        try:
            await self._execute_checked_rcon(command)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            await asyncio.to_thread(
                self._accounts.set_minecraft_xp_observation,
                account_id=account.id,
                current_xp=current_xp,
                observed_at=observed_at,
            )
            LOGGER.warning(
                "Could not apply doubled Minecraft XP to %s: %s",
                account.server_player_name,
                error,
            )

    async def _set_voice_bonus_state(
        self,
        account: MinecraftAccount,
        *,
        active: bool,
        notify: bool = True,
    ) -> None:
        user_id = account.discord_user_id
        if user_id is None:
            return
        should_notify = False
        async with self._voice_bonus_lock:
            was_active = user_id in self._voice_bonus_active_users
            if was_active is active:
                return
            if account.player_uuid is None:
                if active:
                    LOGGER.warning(
                        "Could not activate voice XP bonus without UUID account=%d",
                        account.id,
                    )
                    return
                self._voice_bonus_active_users.discard(user_id)
                return
            try:
                await self._execute_checked_rcon(
                    voice_bonus_state_command(account.player_uuid, active=active)
                )
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning(
                    "Could not set Paper voice XP bonus account=%d active=%s: %s",
                    account.id,
                    active,
                    error,
                )
                return
            if not active:
                self._voice_bonus_active_users.discard(user_id)
                return
            self._voice_bonus_active_users.add(user_id)
            if not notify:
                return
            now = time.monotonic()
            last_notified = self._voice_bonus_last_notified.get(user_id)
            if (
                last_notified is None
                or now - last_notified >= _VOICE_BONUS_NOTIFICATION_COOLDOWN_SECONDS
            ):
                self._voice_bonus_last_notified[user_id] = now
                should_notify = True
        if should_notify:
            await self._announce_voice_bonus_started(account)

    async def _announce_voice_bonus_started(self, account: MinecraftAccount) -> None:
        guild_id = self._settings.guild_id
        user_id = account.discord_user_id
        if guild_id is None or user_id is None:
            return
        guild = self.get_guild(guild_id)
        server_name = guild.name if guild is not None else "サーバー"
        try:
            command = voice_bonus_started_tellraw_command(server_name, account.server_player_name)
            await asyncio.to_thread(self._require_rcon().execute, command)
        except (OSError, RconError, RuntimeError) as error:
            LOGGER.warning("Could not announce voice bonus in Minecraft: %s", error)
        try:
            await self._send(
                format_voice_bonus_started(
                    server_name=server_name,
                    player_name=account.server_player_name,
                    discord_user_id=user_id,
                )
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not announce voice bonus in Discord: %s", error)

    async def _announce_server_xp_started(self, account: MinecraftAccount) -> None:
        guild_id = self._settings.guild_id
        user_id = account.discord_user_id
        if guild_id is None or user_id is None:
            return
        guild = self.get_guild(guild_id)
        server_name = guild.name if guild is not None else "サーバー"
        try:
            command = server_xp_started_tellraw_command(
                server_name,
                account.server_player_name,
            )
            await asyncio.to_thread(self._require_rcon().execute, command)
        except (OSError, RconError, RuntimeError) as error:
            LOGGER.warning("Could not announce server XP in Minecraft: %s", error)
        try:
            await self._send(
                format_server_xp_started(
                    server_name=server_name,
                    player_name=account.server_player_name,
                    discord_user_id=user_id,
                )
            )
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not announce server XP in Discord: %s", error)

    async def _query_player_experience(self, player_name: str) -> int:
        rcon = self._require_rcon()
        levels_response = await asyncio.to_thread(
            rcon.execute, experience_query_command(player_name, "levels")
        )
        points_response = await asyncio.to_thread(
            rcon.execute, experience_query_command(player_name, "points")
        )
        levels = parse_experience_query(levels_response, "levels")
        points = parse_experience_query(points_response, "points")
        return total_experience_points(levels, points)

    async def _deliver_minecraft_xp_outbox(self) -> bool:
        if not self._config.minecraft_bonuses_enabled:
            return True
        while events := await asyncio.to_thread(self._accounts.list_minecraft_xp_outbox):
            for event in events:
                if not await self._level_bot_xp.send(event):
                    return False
                await asyncio.to_thread(self._accounts.mark_minecraft_xp_delivered, event.event_id)
        return True

    async def _enable_player_count_channel(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
    ) -> discord.VoiceChannel:
        self._require_rcon()
        member = guild.me
        if (
            member is None
            or not member.guild_permissions.manage_channels
            or not member.guild_permissions.set_voice_channel_status
        ):
            raise RuntimeError(
                "Botに「チャンネルの管理」と「ボイスチャンネルステータスの設定」権限が必要です"
            )

        channel = await self._get_player_count_channel(guild)
        if channel is None:
            category = getattr(interaction.channel, "category", None)
            if not isinstance(category, discord.CategoryChannel):
                category = None
            channel = await guild.create_voice_channel(
                PLAYER_COUNT_CHANNEL_NAME,
                category=category,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        connect=False,
                        speak=False,
                    )
                },
                reason="Minecraftオンライン人数表示を作成",
            )
        else:
            overwrite = channel.overwrites_for(guild.default_role)
            if overwrite.connect is not False or overwrite.speak is not False:
                overwrite.connect = False
                overwrite.speak = False
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=overwrite,
                    reason="人数表示チャンネルを閲覧専用に設定",
                )

        updated = replace(
            self._settings,
            guild_id=guild.id,
            player_count_channel_id=channel.id,
            player_count_enabled=True,
        )
        await self._save_settings(updated)
        await self._refresh_player_count_channel(channel)
        self._schedule_player_count_name_normalization()
        return channel

    async def _get_player_count_channel(
        self,
        guild: discord.Guild,
    ) -> discord.VoiceChannel | None:
        channel_id = self._settings.player_count_channel_id
        if channel_id is None:
            return None
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                return None
        return channel if isinstance(channel, discord.VoiceChannel) else None

    async def _refresh_player_count_channel(
        self,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        async with self._player_count_update_lock:
            if not self._settings.player_count_enabled:
                return
            guild = self.get_guild(self._settings.guild_id or 0)
            if channel is None:
                if guild is None:
                    raise RuntimeError("設定したDiscordサーバーを取得できません")
                channel = await self._get_player_count_channel(guild)
            if channel is None:
                raise RuntimeError("オンライン人数チャンネルが見つかりません")

            count: int | None = None
            try:
                response = await asyncio.to_thread(self._require_rcon().execute, "list")
                count = parse_online_player_count(response)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                LOGGER.warning("Could not read Minecraft online player count: %s", error)

            status = player_count_status(count)
            if status == self._last_player_count_status:
                return
            await channel.edit(
                status=status,
                reason="Minecraftオンライン人数ステータスを更新",
            )
            self._last_player_count_status = status

    async def _refresh_player_count_channel_safely(self) -> None:
        if not self._settings.player_count_enabled:
            return
        try:
            await self._refresh_player_count_channel()
        except (RuntimeError, discord.DiscordException) as error:
            LOGGER.warning("Could not update player count channel: %s", error)

    async def _read_server_status_snapshot(self) -> ServerStatusSnapshot:
        checked_at = datetime.now(UTC)
        try:
            response = await asyncio.to_thread(self._require_rcon().execute, "list")
            player_names, max_players = parse_server_list_response(response)
        except (OSError, RconError, RuntimeError, ValueError) as error:
            LOGGER.warning("Could not read Minecraft server status: %s", error)
            return ServerStatusSnapshot(
                online=False,
                players=(),
                max_players=None,
                checked_at=checked_at,
            )

        linked = await self._linked_accounts_by_online_name(player_names)
        players = tuple(
            StatusPlayer(
                minecraft_name=player_name,
                discord_user_id=(
                    linked[player_name.casefold()].discord_user_id
                    if player_name.casefold() in linked
                    else None
                ),
            )
            for player_name in sorted(player_names, key=str.casefold)
        )
        return ServerStatusSnapshot(
            online=True,
            players=players,
            max_players=max_players,
            checked_at=checked_at,
        )

    async def _refresh_status_panel(self) -> None:
        async with self._status_panel_update_lock:
            channel_id = self._settings.status_panel_channel_id
            if channel_id is None:
                return
            channel = await self._resolve_and_validate_channel(
                channel_id,
                require_embeds=True,
                require_message_history=True,
            )
            snapshot = await self._read_server_status_snapshot()
            embed = status_panel_embed(snapshot)
            message: discord.Message | None = None
            message_id = self._settings.status_panel_message_id
            if message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    message = None
            if message is None:
                message = await channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self._save_settings(
                    replace(self._settings, status_panel_message_id=message.id)
                )
                return
            await message.edit(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _refresh_status_panel_safely(self) -> None:
        if self._settings.status_panel_channel_id is None:
            return
        try:
            await self._refresh_status_panel()
        except (OSError, RuntimeError, ValueError, discord.DiscordException) as error:
            LOGGER.warning("Could not update Minecraft status panel: %s", error)

    def _schedule_status_panel_refresh(self, *, delay: float = 1) -> None:
        if self._settings.status_panel_channel_id is None:
            return
        if self._status_panel_task is not None and not self._status_panel_task.done():
            return
        self._status_panel_task = asyncio.create_task(
            self._refresh_status_panel_after_delay(delay),
            name="status-panel-refresh",
        )

    def _schedule_periodic_status_panel_refresh(self) -> None:
        now = time.monotonic()
        if now < self._next_status_panel_refresh_at:
            return
        self._next_status_panel_refresh_at = now + _STATUS_PANEL_REFRESH_SECONDS
        self._schedule_status_panel_refresh(delay=0)

    async def _refresh_status_panel_after_delay(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        await self._refresh_status_panel_safely()

    async def _delete_old_status_panel(
        self, channel_id: int | None, message_id: int | None
    ) -> None:
        if channel_id is None or message_id is None:
            return
        try:
            channel = await self._resolve_and_validate_channel(
                channel_id, require_message_history=True
            )
            message = await channel.fetch_message(message_id)
            if self.user is not None and message.author.id == self.user.id:
                await message.delete()
        except RuntimeError, discord.NotFound, discord.DiscordException:
            return

    def _schedule_player_count_refresh(self, *, delay: float = 1) -> None:
        if not self._settings.player_count_enabled:
            return
        if self._player_count_task is not None and not self._player_count_task.done():
            return
        self._player_count_task = asyncio.create_task(
            self._refresh_player_count_after_delay(delay),
            name="player-count-refresh",
        )

    async def _refresh_player_count_after_delay(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        await self._refresh_player_count_channel_safely()

    def _schedule_player_count_name_normalization(self) -> None:
        if self._player_count_name_task is not None and not self._player_count_name_task.done():
            return
        self._player_count_name_task = asyncio.create_task(
            self._normalize_player_count_channel_name(),
            name="player-count-name-normalization",
        )

    async def _normalize_player_count_channel_name(self) -> None:
        try:
            guild = self.get_guild(self._settings.guild_id or 0)
            if guild is None:
                return
            channel = await self._get_player_count_channel(guild)
            if channel is not None and channel.name != PLAYER_COUNT_CHANNEL_NAME:
                await channel.edit(
                    name=PLAYER_COUNT_CHANNEL_NAME,
                    reason="Minecraftオンライン人数をボイスチャンネルステータスへ移行",
                )
        except discord.DiscordException as error:
            LOGGER.warning("Could not normalize player count channel name: %s", error)

    async def _reconcile_pending_actions(self) -> None:
        for account in await asyncio.to_thread(self._accounts.list_pending_actions):
            try:
                if account.status == "pending_add":
                    await self._add_to_whitelist(account)
                else:
                    await self._remove_from_whitelist(account)
            except (OSError, RconError, RuntimeError, ValueError) as error:
                attempts, exhausted = await asyncio.to_thread(
                    self._accounts.record_whitelist_retry_failure,
                    account.id,
                    expected_status=account.status,
                    error=str(error),
                )
                if attempts == 0:
                    continue
                if exhausted:
                    LOGGER.error(
                        "Minecraft account reconciliation stopped after %d attempts for %s: %s",
                        attempts,
                        account.minecraft_name,
                        error,
                    )
                else:
                    LOGGER.warning(
                        "Minecraft account reconciliation retry %d/%d for %s: %s",
                        attempts,
                        WHITELIST_RETRY_LIMIT,
                        account.minecraft_name,
                        error,
                    )

    def _tailer_stopped(self, task: asyncio.Task[None]) -> None:
        self._remove_health_file()
        if self._closing or task.cancelled():
            return
        error = task.exception()
        if error is None:
            LOGGER.error("Minecraft log tailer stopped unexpectedly")
        else:
            LOGGER.error(
                "Minecraft log tailer failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _remove_health_file(self) -> None:
        self._health_path.unlink(missing_ok=True)

    @staticmethod
    def _channel_text(channel_id: int | None) -> str:
        return "未設定" if channel_id is None else f"<#{channel_id}> (`{channel_id}`)"

    @staticmethod
    def _account_line(account: MinecraftAccount) -> str:
        edition = "Java版" if account.edition == "java" else "Bedrock版"
        if account.whitelist_retry_count >= WHITELIST_RETRY_LIMIT:
            state = (
                "追加失敗\uff08自動再試行停止\uff09"
                if account.status == "pending_add"
                else "解除失敗\uff08自動再試行停止\uff09"
                if account.status == "pending_remove"
                else account.status
            )
        else:
            state = {
                "active": "参加可能",
                "pending_approval": "承認待ち",
                "pending_add": "反映待ち",
                "pending_remove": "解除反映待ち",
            }.get(account.status, account.status)
        protection = "・保護" if not account.managed else ""
        name = discord.utils.escape_markdown(account.minecraft_name)
        return f"・**{name}** / {edition} / {state}{protection}"
