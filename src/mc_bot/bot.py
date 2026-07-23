from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

import discord
from discord import app_commands

from mc_bot.config import Config
from mc_bot.events import parse_log_line
from mc_bot.formatting import format_event
from mc_bot.settings import RuntimeSettings, SettingsStore
from mc_bot.tailer import LogTailer
from mc_bot.translations import AdvancementTranslator

LOGGER = logging.getLogger(__name__)


class MinecraftDiscordBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

        self._config = config
        self._translator = AdvancementTranslator.load()
        self._tailer = LogTailer(config.minecraft_log_path, config.cursor_path)
        self._settings_store = SettingsStore(config.settings_path)
        try:
            self._settings = self._settings_store.load()
        except ValueError as error:
            LOGGER.error("Invalid settings; starting unconfigured: %s", error)
            self._settings = RuntimeSettings()
        self._settings_lock = asyncio.Lock()

        self._tailer_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._channel: discord.TextChannel | None = None
        self._delivery_healthy = True
        self._closing = False
        self._health_path = Path("/tmp/mc-bot-healthy")

    def _register_commands(self) -> None:
        group = app_commands.Group(
            name="mc-config",
            description="Minecraftログ通知Botの設定",
            default_permissions=discord.Permissions(manage_guild=True),
            guild_only=True,
        )
        group.command(
            name="channel",
            description="ログの通知先チャンネルを設定します",
        )(self._configure_channel)
        group.command(
            name="show",
            description="現在のBot設定と稼働状態を表示します",
        )(self._show_configuration)
        self.tree.add_command(group)

    async def setup_hook(self) -> None:
        synced = await self.tree.sync()
        LOGGER.info("Synced %d global Discord application commands", len(synced))

    async def on_ready(self) -> None:
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_loop(), name="health-monitor")

        channel_id = self._settings.channel_id
        if channel_id is None:
            self._channel = None
            LOGGER.warning(
                "Notification channel is not configured; use /mc-config channel in Discord"
            )
        else:
            try:
                self._channel = await self._resolve_and_validate_channel(channel_id)
                await self._ensure_tailer_started()
            except (OSError, RuntimeError, discord.DiscordException) as error:
                self._channel = None
                LOGGER.error(
                    "Notification forwarding is inactive; repair it with /mc-config channel: %s",
                    error,
                )

        LOGGER.info(
            "Discord connected as %s; loaded %d advancement translations",
            self.user,
            len(self._translator),
        )

    async def close(self) -> None:
        self._closing = True
        self._remove_health_file()
        if self._health_task is not None:
            self._health_task.cancel()
            await asyncio.gather(self._health_task, return_exceptions=True)
            self._health_task = None
        if self._tailer_task is not None:
            self._tailer_task.cancel()
            await asyncio.gather(self._tailer_task, return_exceptions=True)
            self._tailer_task = None
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
            validated_channel = await self._resolve_and_validate_channel(target.id)
            await asyncio.to_thread(self._tailer.validate)
            async with self._settings_lock:
                updated = replace(self._settings, channel_id=target.id)
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

    async def _show_configuration(self, interaction: discord.Interaction) -> None:
        if not await self._require_server_manager(interaction):
            return
        if self._settings.channel_id is None:
            channel_text = "未設定"
        else:
            channel_text = f"<#{self._settings.channel_id}> (`{self._settings.channel_id}`)"
        forwarding = (
            self._channel is not None
            and self._tailer_task is not None
            and not self._tailer_task.done()
            and self._delivery_healthy
        )
        await interaction.response.send_message(
            "\n".join(
                (
                    f"通知先: {channel_text}",
                    f"ログ転送: {'稼働中' if forwarding else '停止中'}",
                )
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _require_server_manager(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "このコマンドはDiscordサーバー内でのみ使用できます。", ephemeral=True
            )
            return False
        if not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "このコマンドには「サーバーの管理」権限が必要です。", ephemeral=True
            )
            return False
        return True

    async def _ensure_tailer_started(self) -> None:
        if self._tailer_task is not None and not self._tailer_task.done():
            return
        await asyncio.to_thread(self._tailer.validate)
        self._delivery_healthy = True
        self._tailer_task = asyncio.create_task(self._forward_logs(), name="minecraft-log-tailer")
        self._tailer_task.add_done_callback(self._tailer_stopped)

    async def _forward_logs(self) -> None:
        async for pending_line in self._tailer.lines():
            event = parse_log_line(pending_line.text)
            if event is None:
                await asyncio.to_thread(self._tailer.acknowledge, pending_line)
                continue
            content = format_event(event, self._translator)
            retry_delay = 1
            while not self.is_closed():
                await self.wait_until_ready()
                try:
                    await self._send(content)
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
                break

    async def _send(self, content: str) -> None:
        if self._channel is None:
            raise RuntimeError("Discord channel has not been validated")
        await self._channel.send(content, allowed_mentions=discord.AllowedMentions.none())

    async def _resolve_and_validate_channel(self, channel_id: int) -> discord.TextChannel:
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
            raise RuntimeError(
                "The bot needs View Channel and Send Messages permissions in the configured channel"
            )
        return channel

    async def _health_loop(self) -> None:
        while not self.is_closed():
            unconfigured = self._channel is None
            tailer_running = self._tailer_task is not None and not self._tailer_task.done()
            forwarding_healthy = tailer_running and self._delivery_healthy
            if self.is_ready() and (unconfigured or forwarding_healthy):
                self._health_path.touch()
            else:
                self._remove_health_file()
            await asyncio.sleep(10)

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
