from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

import aiohttp
import discord

from mc_bot.deaths import translate_death
from mc_bot.events import EventType, LogEvent
from mc_bot.translations import AdvancementTranslator

LOGGER = logging.getLogger(__name__)
_MAX_SPEECH_LENGTH = 120
_MAX_AUDIO_BYTES = 10 * 1024 * 1024
_MAX_PLAYBACK_SECONDS = 120


class TtsApiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceRequest:
    guild_id: int
    text: str


def event_speech_text(
    event: LogEvent,
    translator: AdvancementTranslator,
    floodgate_prefix: str = ".",
    discord_username: str | None = None,
) -> str:
    linked_name = discord_username.strip() if discord_username else ""
    player_name = linked_name or event.player_name
    if not linked_name and floodgate_prefix and player_name.startswith(floodgate_prefix):
        player_name = player_name.removeprefix(floodgate_prefix)
    honored_name = player_name if player_name.endswith("さん") else f"{player_name}さん"
    match event.type:
        case EventType.CHAT:
            text = " ".join(event.detail.split())
        case EventType.ADVANCEMENT:
            advancement = translator.translate(event.detail)
            text = f"{honored_name}が進捗、{advancement}、を達成しました"
        case EventType.JOIN:
            text = f"{honored_name}がゲームに参加しました"
        case EventType.LEAVE:
            text = f"{honored_name}がゲームから退出しました"
        case EventType.DEATH:
            text = f"{honored_name}が{translate_death(event.detail)}"
    if len(text) <= _MAX_SPEECH_LENGTH:
        return text
    return text[: _MAX_SPEECH_LENGTH - 1] + "…"


class MinecraftVoicePlayer:
    def __init__(
        self,
        client: discord.Client,
        *,
        api_url: str,
        api_token: str,
        speaker_id: int,
        speed: float,
        queue_size: int = 50,
        playback_timeout: float = _MAX_PLAYBACK_SECONDS,
    ) -> None:
        self._client = client
        self._api_url = api_url.rstrip("/")
        self._api_token = api_token
        self._speaker_id = speaker_id
        self._speed = speed
        self._playback_timeout = playback_timeout
        self._queue: asyncio.Queue[VoiceRequest] = asyncio.Queue(maxsize=queue_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._session: aiohttp.ClientSession | None = None

    @property
    def configured(self) -> bool:
        return bool(self._api_url and self._api_token)

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._worker(),
                name="minecraft-voice-player",
            )

    def enqueue(self, guild_id: int, text: str) -> bool:
        if not self.configured or not self.is_connected(guild_id):
            return False
        self.start()
        try:
            self._queue.put_nowait(VoiceRequest(guild_id, text))
        except asyncio.QueueFull:
            LOGGER.warning("Minecraft voice queue is full; dropping newest event")
            return False
        return True

    async def close(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def synthesize(self, request: VoiceRequest) -> bytes:
        if not self.configured:
            raise TtsApiError("VOICEVOX TTS APIが設定されていません")
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        payload = {
            "text": request.text,
            "guild_id": request.guild_id,
            "speaker_id": self._speaker_id,
            "speed": self._speed,
            "cache": False,
        }
        headers = {"Authorization": f"Bearer {self._api_token}"}
        try:
            async with self._session.post(
                f"{self._api_url}/synthesize",
                json=payload,
                headers=headers,
            ) as response:
                if response.status != 200:
                    detail = (await response.text())[:300]
                    raise TtsApiError(f"VOICEVOX TTS API error {response.status}: {detail}")
                content_length = response.content_length
                if content_length is not None and content_length > _MAX_AUDIO_BYTES:
                    raise TtsApiError("VOICEVOX TTS APIの音声が大きすぎます")
                audio = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    if len(audio) + len(chunk) > _MAX_AUDIO_BYTES:
                        raise TtsApiError("VOICEVOX TTS APIの音声が大きすぎます")
                    audio.extend(chunk)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise TtsApiError(f"VOICEVOX TTS APIへ接続できません: {error}") from error
        if not audio:
            raise TtsApiError("VOICEVOX TTS APIが空の音声を返しました")
        return bytes(audio)

    async def _worker(self) -> None:
        while True:
            request = await self._queue.get()
            try:
                voice_client = self._voice_client(request.guild_id)
                if voice_client is None or not voice_client.is_connected():
                    LOGGER.warning(
                        "Minecraft voice event skipped because VC is disconnected guild_id=%d",
                        request.guild_id,
                    )
                    continue
                audio = await self.synthesize(request)
                await self._play(voice_client, audio)
            except (TtsApiError, OSError, discord.DiscordException) as error:
                LOGGER.warning("Minecraft voice playback failed: %s", error)
            except Exception:
                LOGGER.exception("Unexpected Minecraft voice playback failure")
            finally:
                self._queue.task_done()

    def _voice_client(self, guild_id: int) -> discord.VoiceClient | None:
        for voice_client in self._client.voice_clients:
            if voice_client.guild.id == guild_id:
                return voice_client
        return None

    def is_connected(self, guild_id: int) -> bool:
        voice_client = self._voice_client(guild_id)
        return voice_client is not None and voice_client.is_connected()

    async def _play(self, voice_client: discord.VoiceClient, audio: bytes) -> None:
        source: discord.FFmpegPCMAudio | None = None
        completed: asyncio.Future[None] | None = None
        loop = asyncio.get_running_loop()

        def after_playback(error: Exception | None) -> None:
            def finish() -> None:
                if completed.done():
                    return
                if error is None:
                    completed.set_result(None)
                else:
                    completed.set_exception(TtsApiError(f"Discord voice playback error: {error}"))

            loop.call_soon_threadsafe(finish)

        try:
            async with asyncio.timeout(self._playback_timeout):
                while voice_client.is_playing() or voice_client.is_paused():
                    await asyncio.sleep(0.05)
                source = discord.FFmpegPCMAudio(
                    io.BytesIO(audio),
                    pipe=True,
                    before_options="-loglevel error",
                )
                completed = loop.create_future()
                voice_client.play(source, after=after_playback)
                await completed
        except TimeoutError as error:
            voice_client.stop()
            raise TtsApiError("Discord音声の再生がタイムアウトしました") from error
        finally:
            if source is not None:
                source.cleanup()
