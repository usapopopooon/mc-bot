import asyncio

import pytest

import mc_bot.voice as voice_module
from mc_bot.events import EventType, LogEvent
from mc_bot.translations import AdvancementTranslator
from mc_bot.voice import MinecraftVoicePlayer, TtsApiError, VoiceRequest, event_speech_text


class FakeClient:
    def __init__(self, voice_clients=None) -> None:
        self.voice_clients = voice_clients or []


class FakeContent:
    def __init__(self, chunks=None) -> None:
        self.chunks = chunks or [b"wav-data"]

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    status = 200

    def __init__(self, chunks=None) -> None:
        self.content_length = None if chunks is not None else 8
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class FakeSession:
    def __init__(self, chunks=None) -> None:
        self.closed = False
        self.calls = []
        self.chunks = chunks

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.chunks)

    async def close(self) -> None:
        self.closed = True


def test_formats_minecraft_events_for_speech() -> None:
    translator = AdvancementTranslator.load()

    assert event_speech_text(LogEvent(EventType.JOIN, ".hoge"), translator) == "hogeが参加しました"
    assert (
        event_speech_text(LogEvent(EventType.LEAVE, "Steve"), translator) == "Steveが退出しました"
    )
    assert (
        event_speech_text(LogEvent(EventType.ADVANCEMENT, "Steve", "Stone Age"), translator)
        == "Steveが進捗、石器時代、を達成しました"
    )
    assert (
        event_speech_text(LogEvent(EventType.CHAT, "Steve", "こんにちは\n  世界"), translator)
        == "Steve。こんにちは 世界"
    )


def test_prefers_linked_discord_username_for_speech() -> None:
    translator = AdvancementTranslator.load()

    assert (
        event_speech_text(
            LogEvent(EventType.CHAT, ".MinecraftName", "こんにちは"),
            translator,
            ".",
            "discord_name",
        )
        == "discord_name。こんにちは"
    )
    assert (
        event_speech_text(
            LogEvent(EventType.LEAVE, "MinecraftName"),
            translator,
            discord_username="discord_name",
        )
        == "discord_nameが退出しました"
    )
    assert (
        event_speech_text(
            LogEvent(EventType.CHAT, ".MinecraftName", "こんにちは"),
            translator,
            discord_username="   ",
        )
        == "MinecraftName。こんにちは"
    )


def test_limits_speech_to_internal_api_default_limit() -> None:
    text = event_speech_text(
        LogEvent(EventType.CHAT, "Steve", "x" * 200),
        AdvancementTranslator.load(),
    )

    assert len(text) == 120
    assert text.endswith("…")


def test_rejects_enqueue_when_api_token_is_missing() -> None:
    async def exercise() -> None:
        player = MinecraftVoicePlayer(
            FakeClient(),  # type: ignore[arg-type]
            api_url="http://tts:8080",
            api_token="",
            speaker_id=46,
            speed=1.0,
        )

        assert player.enqueue(123, "test") is False
        await player.close()

    asyncio.run(exercise())


def test_sends_authenticated_request_to_internal_tts_api() -> None:
    async def exercise() -> None:
        player = MinecraftVoicePlayer(
            FakeClient(),  # type: ignore[arg-type]
            api_url="http://tts:8080/",
            api_token="tts-secret",
            speaker_id=46,
            speed=1.25,
        )
        session = FakeSession()
        player._session = session  # type: ignore[assignment]

        audio = await player.synthesize(VoiceRequest(123, "テストです"))

        assert audio == b"wav-data"
        assert session.calls == [
            (
                "http://tts:8080/synthesize",
                {
                    "json": {
                        "text": "テストです",
                        "guild_id": 123,
                        "speaker_id": 46,
                        "speed": 1.25,
                        "cache": False,
                    },
                    "headers": {"Authorization": "Bearer tts-secret"},
                },
            )
        ]
        await player.close()

    asyncio.run(exercise())


def test_rejects_streamed_audio_over_size_limit(monkeypatch) -> None:
    async def exercise() -> None:
        voice_client = FakeVoiceClient()
        player = MinecraftVoicePlayer(
            FakeClient([voice_client]),  # type: ignore[arg-type]
            api_url="http://tts:8080",
            api_token="tts-secret",
            speaker_id=46,
            speed=1.0,
        )
        player._session = FakeSession([b"123", b"45"])  # type: ignore[assignment]

        with pytest.raises(TtsApiError, match="大きすぎます"):
            await player.synthesize(VoiceRequest(123, "テストです"))
        await player.close()

    monkeypatch.setattr(voice_module, "_MAX_AUDIO_BYTES", 4)
    asyncio.run(exercise())


class FakeGuild:
    id = 123


class FakeVoiceClient:
    guild = FakeGuild()

    def __init__(self, *, connected=True, playing=False) -> None:
        self.connected = connected
        self.playing = playing
        self.stopped = False

    def is_connected(self) -> bool:
        return self.connected

    def is_playing(self) -> bool:
        return self.playing

    def is_paused(self) -> bool:
        return False

    def stop(self) -> None:
        self.stopped = True
        self.playing = False


class RecordingVoicePlayer(MinecraftVoicePlayer):
    def __init__(self, client) -> None:
        super().__init__(
            client,
            api_url="http://tts:8080",
            api_token="tts-secret",
            speaker_id=46,
            speed=1.0,
        )
        self.synthesized = []
        self.played = []

    async def synthesize(self, request: VoiceRequest) -> bytes:
        self.synthesized.append(request.text)
        if request.text == "失敗":
            raise TtsApiError("test failure")
        return request.text.encode()

    async def _play(self, _voice_client, audio: bytes) -> None:
        self.played.append(audio.decode())


def test_rejects_enqueue_while_voice_is_disconnected() -> None:
    async def exercise() -> None:
        player = MinecraftVoicePlayer(
            FakeClient([FakeVoiceClient(connected=False)]),  # type: ignore[arg-type]
            api_url="http://tts:8080",
            api_token="tts-secret",
            speaker_id=46,
            speed=1.0,
        )

        assert player.enqueue(123, "test") is False
        await player.close()

    asyncio.run(exercise())


def test_worker_preserves_order_and_continues_after_synthesis_error() -> None:
    async def exercise() -> None:
        player = RecordingVoicePlayer(FakeClient([FakeVoiceClient()]))
        player.start()

        assert player.enqueue(123, "失敗") is True
        assert player.enqueue(123, "次の音声") is True
        await asyncio.wait_for(player._queue.join(), timeout=1)

        assert player.synthesized == ["失敗", "次の音声"]
        assert player.played == ["次の音声"]
        await player.close()

    asyncio.run(exercise())


def test_playback_wait_has_timeout() -> None:
    async def exercise() -> None:
        voice_client = FakeVoiceClient(playing=True)
        player = MinecraftVoicePlayer(
            FakeClient([voice_client]),  # type: ignore[arg-type]
            api_url="http://tts:8080",
            api_token="tts-secret",
            speaker_id=46,
            speed=1.0,
            playback_timeout=0.01,
        )

        with pytest.raises(TtsApiError, match="タイムアウト"):
            await player._play(voice_client, b"unused")  # type: ignore[arg-type]
        assert voice_client.stopped is True
        await player.close()

    asyncio.run(exercise())
