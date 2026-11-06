"""Realtime speech through HA streaming and the real SDK WebSocket protocol."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from homeassistant.components import tts
from homeassistant.exceptions import HomeAssistantError
from mistralai.client.errors import NoResponseError
from mistralai.client.models import Security, VoiceResponse
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from custom_components.mistral_conversation.api import async_stream_speech
from custom_components.mistral_conversation.const import (
    CONF_NORMALIZE_AUDIO,
    CONF_SYNTHESIS_MODE,
    ApiErrorKind,
    MistralModel,
)
from custom_components.mistral_conversation.errors import (
    RealtimeError,
    SpeechStreamError,
)

from .helpers import mistral_error
from .test_tts import ENTITY_ID, _entity
from .test_tts import tts_component as tts_component  # noqa: PLC0414 -- pytest fixture


def voice_record(voice_id="test-voice", slug=None):
    return VoiceResponse(
        id=voice_id,
        slug=slug,
        name="Test voice",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        type="preset",
        user_id=None,
    )


def session(**settings):
    """Use the complete session schema required by SDK 3.7."""
    return {
        "request_id": "test-request",
        "model": "test-model",
        "voice": "test-voice",
        "is_ref_audio": False,
        "response_format": "mp3",
        "streaming_mode": "responsive",
        "expressiveness": 1.0,
        **settings,
    }


def delta(data=b"audio"):
    return {"type": "speech.audio.delta", "audio_data": base64.b64encode(data).decode()}


def done():
    return {"type": "speech.audio.done", "usage": {}}


async def text(*chunks):
    for chunk in chunks:
        yield chunk


class SpeechSocket:
    """Mock the socket only; SDK serialization and event parsing stay real."""

    def __init__(self):
        self.messages = asyncio.Queue()
        self.messages.put_nowait(
            json.dumps({"type": "session.created", "session": session()})
        )
        self.sent = []
        self.closed = False
        self.close_code = None
        self.server_close_code = 1000
        self.server_close_error = None
        self.connect_delay = 0
        self.ack = None
        self.final = [done()]
        self.on_append = [delta()]
        self.end_received = asyncio.Event()
        self.receiving = asyncio.Event()
        self.setup_delay = 0
        self.hold_end = False

    async def recv(self):
        self.receiving.set()
        value = await self.messages.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        if message["type"] == "session.update":
            await asyncio.sleep(self.setup_delay)
            ack = self.ack or {
                "type": "session.updated",
                "session": session(**message["session"]),
            }
            self.messages.put_nowait(json.dumps(ack))
        if message["type"] == "input_text.append":
            for event in self.on_append:
                self.messages.put_nowait(json.dumps(event))
        if message["type"] == "input_text.end":
            self.end_received.set()
            if not self.hold_end:
                for event in self.final:
                    self.messages.put_nowait(json.dumps(event))
                self.close_code = self.server_close_code
                if self.server_close_error is not None:
                    self.messages.put_nowait(self.server_close_error)
                self.messages.put_nowait(None)

    async def close(self, **kwargs):
        self.closed = True
        self.close_code = self.close_code or 1000

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.recv()
        if value is None:
            raise StopAsyncIteration
        return value


@pytest.fixture
def speech_transport(monkeypatch):
    socket = SpeechSocket()
    client = MagicMock()
    client.sdk_configuration.get_server_details.return_value = (
        "https://api.mistral.ai",
        {},
    )
    client.sdk_configuration.security = Security(api_key="test-api-key")
    client.sdk_configuration.user_agent = "test-agent"
    client.audio.voices.get_async = AsyncMock(return_value=voice_record())
    calls = []

    @asynccontextmanager
    async def connect(url, **kwargs):
        calls.append((url, kwargs))
        await asyncio.sleep(socket.connect_delay)
        try:
            yield socket
        finally:
            await socket.close()

    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.websocket_connect", connect
    )
    return client, socket, calls


async def collect(client, source=None, output_format="mp3"):
    stream = async_stream_speech(
        client,
        "test-model",
        "test-voice",
        output_format,
        text("Hello") if source is None else source,
    )
    return b"".join([chunk async for chunk in stream])


async def test_incremental_text_and_audio_before_text_finishes(speech_transport):
    client, socket, calls = speech_transport
    first_audio = asyncio.Event()
    input_closed = asyncio.Event()

    async def source():
        try:
            yield "Hello "
            await first_audio.wait()
            assert not socket.end_received.is_set()
            yield "world."
        finally:
            input_closed.set()

    stream = async_stream_speech(client, "test-model", "test-voice", "mp3", source())
    assert await asyncio.wait_for(anext(stream), 1) == b"audio"
    assert not input_closed.is_set()
    first_audio.set()
    assert b"".join([chunk async for chunk in stream]) == b"audio"
    assert [m["text"] for m in socket.sent if m["type"] == "input_text.append"] == [
        "Hello ",
        "world.",
    ]
    assert [m["type"] for m in socket.sent] == [
        "session.update",
        "input_text.append",
        "input_text.append",
        "input_text.end",
    ]
    assert input_closed.is_set() and socket.closed
    url, options = calls[0]
    assert url == "wss://api.mistral.ai/v1/audio/speech/realtime?model=test-model"
    assert options["ssl"].check_hostname
    assert options["additional_headers"] == {"Authorization": "Bearer test-api-key"}
    assert options["max_queue"] == 1
    client.audio.voices.get_async.assert_not_awaited()


@pytest.mark.parametrize("reverse", [False, True])
async def test_voice_uuid_and_slug_are_verified_without_replacing_selection(
    speech_transport, reverse
):
    client, socket, _ = speech_transport
    voice_id, slug = "00000000-0000-4000-8000-000000000001", "voice_123"
    requested, acknowledged = (slug, voice_id) if reverse else (voice_id, slug)
    client.audio.voices.get_async.return_value = voice_record(voice_id, slug)
    socket.ack = {"type": "session.updated", "session": session(voice=acknowledged)}
    stream = async_stream_speech(client, "test-model", requested, "mp3", text("Hello"))
    assert b"".join([chunk async for chunk in stream]) == b"audio"
    assert socket.sent[0]["session"]["voice"] == requested
    client.audio.voices.get_async.assert_awaited_once_with(
        voice_id=requested, timeout_ms=10000
    )
    assert socket.closed


@pytest.mark.parametrize(
    ("record_id", "slug", "acknowledged"),
    [
        ("test-voice", None, "other"),
        ("test-voice", "", "other"),
        ("test-voice", "voice_123", "other"),
        ("unrelated", "voice_123", "voice_123"),
    ],
)
async def test_unverified_voice_acknowledgement_never_sends_text(
    speech_transport, record_id, slug, acknowledged
):
    client, socket, _ = speech_transport
    client.audio.voices.get_async.return_value = voice_record(record_id, slug)
    socket.ack = {"type": "session.updated", "session": session(voice=acknowledged)}
    with pytest.raises(SpeechStreamError):
        await collect(client)
    assert [message["type"] for message in socket.sent] == ["session.update"]
    assert socket.closed


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (mistral_error(401, "private"), ApiErrorKind.AUTHENTICATION),
        (mistral_error(403, "private"), ApiErrorKind.AUTHENTICATION),
        (mistral_error(404, "private"), ApiErrorKind.API),
        (mistral_error(429, "private"), ApiErrorKind.RATE_LIMIT),
        (mistral_error(503, "private"), ApiErrorKind.CONNECTION),
        (NoResponseError("private"), ApiErrorKind.CONNECTION),
        (httpx2.ConnectError("private"), ApiErrorKind.CONNECTION),
        (httpx2.ReadTimeout("private"), ApiErrorKind.TIMEOUT),
    ],
)
async def test_voice_lookup_errors_keep_classification_and_privacy(
    speech_transport, failure, kind, caplog
):
    client, socket, _ = speech_transport
    socket.ack = {"type": "session.updated", "session": session(voice="voice_123")}
    client.audio.voices.get_async.side_effect = failure
    with pytest.raises(RealtimeError) as error:
        await collect(client)
    assert error.value.kind == kind
    assert "private" not in str(error.value) + caplog.text
    assert [message["type"] for message in socket.sent] == ["session.update"]
    assert socket.closed


@pytest.mark.parametrize("cancel", [False, True])
async def test_voice_resolution_is_bounded_and_cancellable(
    speech_transport, monkeypatch, cancel
):
    client, socket, _ = speech_transport
    started = asyncio.Event()

    async def lookup(**kwargs):
        started.set()
        await asyncio.Event().wait()

    client.audio.voices.get_async.side_effect = lookup
    socket.ack = {"type": "session.updated", "session": session(voice="voice_123")}
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.SETUP_TIMEOUT_MS", 50
    )
    task = asyncio.create_task(collect(client))
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RealtimeError) as error:
            await task
        assert error.value.kind == ApiErrorKind.TIMEOUT
    assert socket.closed
    assert [message["type"] for message in socket.sent] == ["session.update"]


@pytest.mark.parametrize(
    "ack",
    [
        {"type": "error", "error": {"code": 401, "message": "private"}},
        {"type": "session.updated", "session": session(voice="other")},
        {"type": "session.updated", "session": session(voice=None)},
        {
            "type": "session.updated",
            "session": {
                key: value for key, value in session().items() if key != "voice"
            },
        },
        {"type": "session.updated", "session": session(response_format="wav")},
        {"type": "session.updated", "session": session(streaming_mode="long_form")},
        {"type": "session.updated", "session": session(is_ref_audio=True)},
    ],
)
async def test_configuration_ack_before_sending_text(speech_transport, ack):
    client, socket, _ = speech_transport
    socket.ack = ack
    with pytest.raises(SpeechStreamError):
        await collect(client)
    assert [m["type"] for m in socket.sent] == ["session.update"]
    assert socket.closed


@pytest.mark.parametrize("handshake", ["broken-json", "[]", '{"type":"other"}'])
async def test_invalid_handshake(speech_transport, handshake):
    client, socket, _ = speech_transport
    socket.messages.get_nowait()
    socket.messages.put_nowait(handshake)
    with pytest.raises(SpeechStreamError):
        await collect(client)
    assert not socket.sent and socket.closed


@pytest.mark.parametrize(
    ("events", "key"),
    [
        ([{"type": "unknown"}], "tts_response_invalid"),
        (
            [{"type": "speech.audio.delta", "audio_data": "not base64"}],
            "tts_response_invalid",
        ),
        ([delta()], "tts_response_invalid"),
        ([done()], "tts_response_empty"),
        ([], "tts_response_invalid"),
    ],
)
async def test_invalid_final_stream(speech_transport, events, key):
    client, socket, _ = speech_transport
    socket.on_append = []
    socket.final = events
    with pytest.raises(SpeechStreamError) as error:
        await collect(client)
    assert error.value.translation_key == key
    assert socket.closed


async def test_rejects_early_done(speech_transport):
    client, socket, _ = speech_transport
    socket.on_append = [delta(), done()]

    async def source():
        yield "Hello"
        await asyncio.Event().wait()

    with pytest.raises(SpeechStreamError):
        await collect(client, source())
    assert socket.closed and not socket.end_received.is_set()


@pytest.mark.parametrize(
    ("chunks", "key"),
    [
        ((), "tts_response_empty"),
        (("abc", "def"), "tts_text_too_long"),
        ((123,), "tts_response_invalid"),
    ],
)
async def test_text_boundaries(speech_transport, monkeypatch, chunks, key):
    client, socket, _ = speech_transport
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.MAX_TTS_TEXT_LENGTH", 5
    )
    with pytest.raises(SpeechStreamError) as error:
        await collect(client, text(*chunks))
    assert error.value.translation_key == key
    assert all(len(m.get("text", "")) <= 5 for m in socket.sent)
    assert socket.closed


async def test_audio_limit_is_cumulative(speech_transport, monkeypatch):
    client, socket, _ = speech_transport
    socket.on_append = [delta(b"ab"), delta(b"cd")]
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.MAX_TTS_AUDIO_BYTES", 3
    )
    with pytest.raises(SpeechStreamError) as error:
        await collect(client)
    assert error.value.translation_key == "tts_audio_too_large"
    assert socket.closed


@pytest.mark.parametrize("final_seen", [False, True])
async def test_tcp_eof_requires_verified_terminal_event(speech_transport, final_seen):
    client, socket, _ = speech_transport
    socket.final = [done()] if final_seen else []
    socket.server_close_error = ConnectionClosedError(None, None)
    if final_seen:
        assert await collect(client) == b"audio"
    else:
        with pytest.raises(RealtimeError):
            await collect(client)
    assert socket.closed


async def test_done_closes_the_single_generation_without_consuming_more_audio(
    speech_transport,
):
    client, socket, _ = speech_transport
    socket.on_append = []
    socket.final = [delta(b"first"), done(), delta(b"second")]
    assert await collect(client) == b"first"
    assert socket.closed


async def test_handshake_and_configuration_share_setup_deadline(
    speech_transport, monkeypatch
):
    client, socket, _ = speech_transport
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.SETUP_TIMEOUT_MS", 50
    )
    socket.connect_delay = 0.035
    socket.setup_delay = 0.035
    with pytest.raises(RealtimeError) as error:
        await collect(client)
    assert error.value.kind == ApiErrorKind.TIMEOUT
    assert [m["type"] for m in socket.sent] == ["session.update"]
    assert socket.closed


async def test_exact_limits_and_empty_text_deltas(speech_transport, monkeypatch):
    client, socket, _ = speech_transport
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.MAX_TTS_TEXT_LENGTH", 5
    )
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.MAX_TTS_AUDIO_BYTES", 5
    )
    assert await collect(client, text("", "Hello", "")) == b"audio"
    assert [m["text"] for m in socket.sent if m["type"] == "input_text.append"] == [
        "Hello"
    ]


async def test_input_failure_cancels_receiver_and_closes_source(speech_transport):
    client, socket, _ = speech_transport
    source_closed = asyncio.Event()

    async def failing_source():
        try:
            yield "Hello"
            raise RuntimeError("private input")
        finally:
            source_closed.set()

    with pytest.raises(RealtimeError) as error:
        await collect(client, failing_source())
    assert "private input" not in str(error.value)
    assert socket.closed and source_closed.is_set()


@pytest.mark.parametrize("phase", ["handshake", "ack", "end", "consumer"])
async def test_deadlines_close_sockets(speech_transport, monkeypatch, phase):
    client, socket, _ = speech_transport
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.SETUP_TIMEOUT_MS", 20
    )
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.REQUEST_TIMEOUT_MS", 70
    )
    if phase == "handshake":
        socket.messages.get_nowait()
    if phase == "ack":
        socket.setup_delay = 1
    if phase == "end":
        socket.hold_end = True
    if phase == "consumer":
        socket.hold_end = True
        socket.on_append = [delta()] * 10
        stream = async_stream_speech(
            client, "test-model", "test-voice", "mp3", text("Hello")
        )
        assert (await anext(stream)).startswith(b"audio")
        await asyncio.sleep(0.12)
        assert socket.closed
        with pytest.raises(RealtimeError) as error:
            await anext(stream)
    else:
        with pytest.raises(RealtimeError) as error:
            await collect(client)
    assert error.value.kind == ApiErrorKind.TIMEOUT
    assert socket.closed


@pytest.mark.parametrize("phase", ["handshake", "text", "audio", "paused"])
async def test_cancellation_and_generator_close(speech_transport, phase):
    client, socket, _ = speech_transport
    source_closed = asyncio.Event()
    waiting = asyncio.Event()

    async def source():
        try:
            yield "Hello"
            if phase == "text":
                waiting.set()
                await asyncio.Event().wait()
        finally:
            source_closed.set()

    if phase == "handshake":
        socket.messages.get_nowait()
    socket.hold_end = True
    stream = async_stream_speech(client, "test-model", "test-voice", "mp3", source())
    if phase != "handshake":
        assert await anext(stream) == b"audio"
    if phase == "paused":
        await stream.aclose()
    else:
        task = asyncio.create_task(anext(stream))
        if phase == "handshake":
            await socket.receiving.wait()
        else:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert socket.closed
    if phase != "handshake":
        assert source_closed.is_set()


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (
            InvalidStatus(Response(401, "unauthorized", Headers())),
            ApiErrorKind.AUTHENTICATION,
        ),
        (
            InvalidStatus(Response(403, "forbidden", Headers())),
            ApiErrorKind.AUTHENTICATION,
        ),
        (InvalidStatus(Response(429, "limited", Headers())), ApiErrorKind.RATE_LIMIT),
        (
            InvalidStatus(Response(503, "unavailable", Headers())),
            ApiErrorKind.CONNECTION,
        ),
        (OSError("private"), ApiErrorKind.CONNECTION),
        (ConnectionClosedError(Close(1008, "private"), None), ApiErrorKind.API),
        (ConnectionClosedError(Close(1011, "private"), None), ApiErrorKind.CONNECTION),
    ],
)
async def test_transport_errors_are_classified_and_private(
    speech_transport, failure, kind, caplog
):
    client, socket, _ = speech_transport
    socket.messages.get_nowait()
    socket.messages.put_nowait(failure)
    with pytest.raises(RealtimeError) as error:
        await collect(client)
    assert error.value.kind == kind
    assert "private" not in str(error.value)
    assert "private" not in caplog.text
    assert socket.closed


async def test_explicit_provider_error_is_not_an_http_status(speech_transport):
    client, socket, _ = speech_transport
    socket.final = [{"type": "error", "error": {"code": 401, "message": "private"}}]
    with pytest.raises(RealtimeError) as error:
        await collect(client)
    assert error.value.kind == ApiErrorKind.API
    assert "private" not in str(error.value)


async def test_reject_insecure_speech_endpoint(speech_transport):
    client, socket, calls = speech_transport
    client.sdk_configuration.get_server_details.return_value = (
        "http://api.mistral.ai",
        {},
    )
    with pytest.raises(RealtimeError):
        await collect(client)
    assert not calls and not socket.sent


@pytest.fixture
async def realtime_entity(hass, tts_component, speech_transport):
    entry, _ = tts_component
    entity = _entity(hass)
    hass.config_entries.async_update_subentry(
        entry,
        entity.subentry,
        data={**entity.subentry.data, CONF_SYNTHESIS_MODE: "realtime"},
    )
    await hass.async_block_till_done()
    entity = _entity(hass)
    # The entity's SDK mock uses the real transport fixture's configuration.
    entity.coordinator.client.sdk_configuration = speech_transport[0].sdk_configuration
    return entity


async def test_ha_public_stream_is_incremental(hass, realtime_entity, speech_transport):
    _, socket, _ = speech_transport
    first_audio = asyncio.Event()

    async def source():
        yield "Hello "
        await first_audio.wait()
        assert not socket.end_received.is_set()
        yield "world."

    stream = tts.async_create_stream(
        hass, ENTITY_ID, options={tts.ATTR_PREFERRED_FORMAT: "mp3"}
    )
    assert stream.supports_streaming_input
    stream.async_set_message_stream(source())
    audio = stream.async_stream_result()
    assert await asyncio.wait_for(anext(audio), 1) == b"audio"
    first_audio.set()
    assert b"".join([chunk async for chunk in audio]) == b"audio"
    assert socket.closed
    realtime_entity.coordinator.client.audio.speech.complete_async.assert_not_awaited()


@pytest.mark.parametrize("override", [False, True])
async def test_public_tts_preserves_voice_uuid_with_verified_slug_ack(
    hass, realtime_entity, speech_transport, override
):
    client, socket, _ = speech_transport
    requested = "test-override-id" if override else "voice-1"
    client.audio.voices.get_async.return_value = voice_record(requested, "voice_123")
    realtime_entity.coordinator.client.audio.voices.get_async = (
        client.audio.voices.get_async
    )
    socket.ack = {"type": "session.updated", "session": session(voice="voice_123")}
    options = {tts.ATTR_PREFERRED_FORMAT: "mp3"}
    if override:
        options[tts.ATTR_VOICE] = requested
    stream = tts.async_create_stream(hass, ENTITY_ID, options=options)
    stream.async_set_message("Hello")
    assert b"".join([chunk async for chunk in stream.async_stream_result()]) == b"audio"
    assert socket.sent[0]["session"]["voice"] == requested
    client.audio.voices.get_async.assert_awaited_once_with(
        voice_id=requested, timeout_ms=10000
    )
    realtime_entity.coordinator.client.audio.speech.complete_async.assert_not_awaited()


@pytest.mark.parametrize(
    ("preferred", "extension", "provider"),
    [
        ("mp3", "mp3", "mp3"),
        ("wav", "wav", "wav"),
        ("flac", "flac", "flac"),
        ("ogg", "ogg", "opus"),
        ("oga", "oga", "opus"),
        ("opus", "opus", "opus"),
        ("raw", "pcm", "pcm"),
        ("pcm", "pcm", "pcm"),
    ],
)
async def test_realtime_formats_and_direct_audio(
    realtime_entity, speech_transport, preferred, extension, provider
):
    _, socket, _ = speech_transport
    assert await realtime_entity.async_get_tts_audio(
        "Hello", "en-US", {tts.ATTR_PREFERRED_FORMAT: preferred}
    ) == (extension, b"audio")
    assert socket.sent[0]["session"]["response_format"] == provider
    assert socket.sent[0]["session"]["voice"] == "voice-1"


async def test_ha_converts_provider_wav_for_satellite(
    hass, realtime_entity, speech_transport
):
    _, socket, _ = speech_transport
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x01" * 24000)
    raw = buffer.getvalue()
    socket.on_append = [delta(raw[:1000]), delta(raw[1000:])]
    stream = tts.async_create_stream(
        hass,
        ENTITY_ID,
        options={
            tts.ATTR_PREFERRED_FORMAT: "wav",
            tts.ATTR_PREFERRED_SAMPLE_RATE: 16000,
            tts.ATTR_PREFERRED_SAMPLE_CHANNELS: 1,
            tts.ATTR_PREFERRED_SAMPLE_BYTES: 2,
        },
    )
    stream.async_set_message("Synthetic test")
    converted = b"".join([chunk async for chunk in stream.async_stream_result()])
    with wave.open(io.BytesIO(converted), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert len(wav.readframes(20000)) >= 32000
    assert socket.closed


async def test_realtime_rejects_normalization_before_connection(
    realtime_entity, speech_transport
):
    _, _, calls = speech_transport
    with pytest.raises(HomeAssistantError) as error:
        await realtime_entity.async_stream_tts_audio(
            tts.TTSAudioRequest("en-US", {CONF_NORMALIZE_AUDIO: True}, text("Hello"))
        )
    assert error.value.translation_key == "tts_realtime_normalization"
    assert not calls


@pytest.mark.parametrize(
    ("metadata", "options", "key"),
    [
        ({"audio_speech_modes": ("offline",)}, {}, "model_no_synthesis_mode"),
        ({"audio_speech_formats": ("wav",)}, {}, "tts_format_unsupported"),
        ({"audio_speech": False}, {}, "model_no_speech"),
    ],
)
async def test_discovered_limits_are_authoritative(
    realtime_entity, speech_transport, metadata, options, key
):
    _, _, calls = speech_transport
    realtime_entity.coordinator.async_set_updated_data(
        [MistralModel(id=realtime_entity.model, **{"audio_speech": True, **metadata})]
    )
    with pytest.raises(HomeAssistantError) as error:
        await realtime_entity.async_get_tts_audio("Hello", "en-US", options)
    assert error.value.translation_key == key
    assert not calls


@pytest.mark.parametrize("paused", [False, True])
async def test_entity_unload_reaps_active_or_paused_request(
    hass, realtime_entity, speech_transport, paused
):
    _, socket, _ = speech_transport
    socket.hold_end = True
    response = await realtime_entity.async_stream_tts_audio(
        tts.TTSAudioRequest("en-US", {}, text("Hello"))
    )
    assert await anext(response.data_gen) == b"audio"
    task = None if paused else asyncio.create_task(anext(response.data_gen))
    await asyncio.sleep(0)
    assert await hass.config_entries.async_unload(realtime_entity.entry.entry_id)
    if task is not None:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(asyncio.CancelledError):
            await anext(response.data_gen)
    assert socket.closed


async def test_failed_ha_stream_is_not_a_successful_cache(
    hass, realtime_entity, speech_transport
):
    _, socket, _ = speech_transport
    socket.hold_end = True
    stream = tts.async_create_stream(hass, ENTITY_ID)
    stream.async_set_message("Synthetic failure")
    audio = stream.async_stream_result()
    assert await anext(audio) == b"audio"
    socket.messages.put_nowait(
        json.dumps({"type": "error", "error": {"code": 500, "message": "private"}})
    )
    with pytest.raises(HomeAssistantError):
        _ = [chunk async for chunk in audio]
    with pytest.raises(HomeAssistantError):
        _ = [chunk async for chunk in stream.async_stream_result()]
    assert realtime_entity.available
    assert socket.closed
