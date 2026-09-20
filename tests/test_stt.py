"""Tests for the Mistral speech-to-text platform."""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncIterable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    DEFAULT_STT_MODEL,
    REALTIME_STT_MODEL,
    REQUEST_TIMEOUT_MS,
)

from .helpers import mistral_error
from .test_realtime import Socket, connection

ENTITY_ID = "stt.mistral_speech_to_text"

pytestmark = pytest.mark.usefixtures("mock_realtime_connect")


async def _audio_stream(*chunks: bytes) -> AsyncIterable[bytes]:
    """Yield raw Assist audio chunks."""
    for chunk in chunks:
        yield chunk


def _metadata(language: str = "en-US") -> stt.SpeechMetadata:
    """Return the exact audio format advertised by the entity."""
    return stt.SpeechMetadata(
        language=language,
        format=stt.AudioFormats.WAV,
        codec=stt.AudioCodecs.PCM,
        bit_rate=stt.AudioBitRates.BITRATE_16,
        sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
        channel=stt.AudioChannels.CHANNEL_MONO,
    )


async def test_stt_properties(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """The entity advertises a conservative Assist-compatible PCM format."""
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)

    assert entity is not None
    assert "en-US" in entity.supported_languages
    assert "fr-FR" in entity.supported_languages
    assert entity.supported_formats == [stt.AudioFormats.WAV]
    assert entity.supported_codecs == [stt.AudioCodecs.PCM]
    assert entity.supported_bit_rates == [stt.AudioBitRates.BITRATE_16]
    assert entity.supported_sample_rates == [stt.AudioSampleRates.SAMPLERATE_16000]
    assert entity.supported_channels == [stt.AudioChannels.CHANNEL_MONO]


async def test_stt_transcribes_wav_with_metadata_language(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """Raw Assist frames receive a WAV header and use the requested language."""
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)
    mock_init_component.audio.transcriptions.complete_async.return_value = (
        SimpleNamespace(text="Bonjour depuis Mistral")
    )

    result = await entity.async_process_audio_stream(
        _metadata("fr-FR"),
        _audio_stream(b"\x01\x02", b"\x03\x04"),
    )

    assert result.result is stt.SpeechResultState.SUCCESS
    assert result.text == "Bonjour depuis Mistral"
    call = mock_init_component.audio.transcriptions.complete_async.await_args
    assert call.kwargs["model"] == DEFAULT_STT_MODEL
    assert call.kwargs["language"] == "fr"
    assert call.kwargs["timeout_ms"] == REQUEST_TIMEOUT_MS
    upload = call.kwargs["file"]
    assert upload["file_name"] == "home-assistant-voice.wav"
    assert upload["content_type"] == "audio/wav"
    with wave.open(io.BytesIO(upload["content"]), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.readframes(wav_file.getnframes()) == b"\x01\x02\x03\x04"


async def test_stt_provider_error_updates_availability(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Provider failures return an STT error and update coordinator health."""
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)
    mock_init_component.audio.transcriptions.complete_async.side_effect = mistral_error(
        503, "offline"
    )

    result = await entity.async_process_audio_stream(
        _metadata(),
        _audio_stream(b"\x00\x00"),
    )

    assert result == stt.SpeechResult(None, stt.SpeechResultState.ERROR)
    assert mock_config_entry.runtime_data.last_update_success is False


@pytest.mark.parametrize("response_text", ["", None])
async def test_stt_empty_provider_response(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    response_text: str | None,
) -> None:
    """An absent provider transcript cannot be reported as successful."""
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)
    mock_init_component.audio.transcriptions.complete_async.return_value = (
        SimpleNamespace(text=response_text)
    )

    result = await entity.async_process_audio_stream(
        _metadata(),
        _audio_stream(b"\x00\x00"),
    )

    assert result.result is stt.SpeechResultState.ERROR
    assert result.text is None


async def test_stt_rejects_empty_and_oversized_audio(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local limits reject unusable audio before a provider request."""
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)

    empty = await entity.async_process_audio_stream(_metadata(), _audio_stream())
    assert empty.result is stt.SpeechResultState.ERROR

    monkeypatch.setattr(
        "custom_components.mistral_conversation.stt.MAX_STT_AUDIO_BYTES",
        3,
    )
    oversized = await entity.async_process_audio_stream(
        _metadata(),
        _audio_stream(b"ab", b"cd"),
    )
    assert oversized.result is stt.SpeechResultState.ERROR
    mock_init_component.audio.transcriptions.complete_async.assert_not_awaited()


@pytest.mark.parametrize(
    "selected_model", [REALTIME_STT_MODEL, "custom-realtime-model"]
)
async def test_stt_routes_exact_realtime_model(
    hass: HomeAssistant, mock_init_component: MagicMock, selected_model: str
) -> None:
    """Only the explicitly supported model changes transport; custom IDs stay valid."""
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)
    entity.model = selected_model
    socket = Socket()
    mock_init_component.audio.realtime.connect = AsyncMock(
        return_value=connection(socket)
    )
    mock_init_component.audio.transcriptions.complete_async.return_value = (
        SimpleNamespace(text="Hello")
    )
    result = await entity.async_process_audio_stream(_metadata(), _audio_stream(b"ab"))
    assert result == stt.SpeechResult("Hello", stt.SpeechResultState.SUCCESS)
    if selected_model == REALTIME_STT_MODEL:
        assert socket.closed
        mock_init_component.audio.transcriptions.complete_async.assert_not_awaited()
    else:
        mock_init_component.audio.realtime.connect.assert_not_awaited()


@pytest.mark.parametrize("phase", ["connect", "upload", "final"])
async def test_stt_unload_cancels_active_realtime_requests(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    phase: str,
) -> None:
    """Entity removal reclaims connecting, uploading and final-waiting requests."""
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)
    entity.model = REALTIME_STT_MODEL
    socket = Socket(hold_final=phase == "final")
    started = asyncio.Event()
    finish_connect = asyncio.Event()

    async def connect(**kwargs):
        started.set()
        if phase == "connect":
            await finish_connect.wait()
        return connection(socket)

    mock_init_component.audio.realtime.connect = AsyncMock(side_effect=connect)

    async def endless_audio():
        yield b"ab"
        if phase != "final":
            await asyncio.Event().wait()

    request = asyncio.create_task(
        entity.async_process_audio_stream(_metadata(), endless_audio())
    )
    await started.wait()
    if phase == "upload":
        await socket.audio_sent.wait()
    elif phase == "final":
        await socket.finished.wait()
        await asyncio.sleep(0)
    unload = asyncio.create_task(
        hass.config_entries.async_unload(mock_config_entry.entry_id)
    )
    assert await unload
    with pytest.raises(asyncio.CancelledError):
        await request
    assert socket.closed is (phase != "connect")
    assert (
        await entity.async_process_audio_stream(_metadata(), _audio_stream(b"ab"))
    ).result is stt.SpeechResultState.ERROR


async def test_stt_realtime_connection_failure_updates_health(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    entity = hass.data[stt.DOMAIN].get_entity(ENTITY_ID)
    entity.model = REALTIME_STT_MODEL
    mock_init_component.audio.realtime.connect = AsyncMock(
        side_effect=OSError("private-data")
    )
    result = await entity.async_process_audio_stream(_metadata(), _audio_stream(b"ab"))
    assert result.result is stt.SpeechResultState.ERROR
    assert not mock_config_entry.runtime_data.last_update_success
