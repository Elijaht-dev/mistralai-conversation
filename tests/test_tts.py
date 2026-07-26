"""Tests for the Mistral text-to-speech platform."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import tts
from homeassistant.const import CONF_API_KEY, CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from mistralai.client.models import SpeechStreamAudioDelta, SpeechStreamEvents
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    CONF_VOICE_ID,
    DEFAULT_TTS_MODEL,
    DOMAIN,
    MAX_TTS_TEXT_LENGTH,
    REQUEST_TIMEOUT_MS,
    SUBENTRY_TYPE_TTS,
    MistralVoice,
)

from .helpers import mistral_error

ENTITY_ID = "tts.mistral_text_to_speech"


def _speech_stream(*chunks: str) -> MagicMock:
    """Return an async context-managed SDK speech event stream."""
    events = [
        SpeechStreamEvents(
            event="speech.audio.delta",
            data=SpeechStreamAudioDelta(audio_data=chunk),
        )
        for chunk in chunks
    ]
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=None)
    stream.__aiter__.return_value = events
    return stream


@pytest.fixture
async def tts_component(
    hass: HomeAssistant,
    mock_provider: MagicMock,
) -> AsyncGenerator[tuple[MockConfigEntry, MagicMock]]:
    """Set up an account with an explicitly selected saved voice."""
    entry = MockConfigEntry(
        title="Mistral AI",
        domain=DOMAIN,
        data={CONF_API_KEY: "test-api-key"},
        version=1,
        minor_version=3,
        subentries_data=[
            {
                "data": {
                    CONF_MODEL: DEFAULT_TTS_MODEL,
                    CONF_VOICE_ID: "voice-1",
                },
                "subentry_type": SUBENTRY_TYPE_TTS,
                "title": "Mistral text-to-speech",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.mistral_conversation.tts.async_get_voices",
        new_callable=AsyncMock,
        return_value=[
            MistralVoice(id="voice-1", name="Home", languages=("en", "fr")),
            MistralVoice(id="voice-2", name="Guest"),
        ],
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    yield entry, mock_provider


def _entity(hass: HomeAssistant) -> Any:
    """Return the initialized TTS entity."""
    entity = hass.data[tts.DOMAIN].get_entity(ENTITY_ID)
    assert entity is not None
    return entity


async def test_tts_properties_and_saved_voices(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
) -> None:
    """The selected voice is the default and account voices are selectable."""
    entity = _entity(hass)

    assert entity.default_language == "en-US"
    assert "fr-FR" in entity.supported_languages
    assert entity.supported_options == [tts.ATTR_VOICE, tts.ATTR_PREFERRED_FORMAT]
    assert entity.default_options == {
        tts.ATTR_VOICE: "voice-1",
        tts.ATTR_PREFERRED_FORMAT: "mp3",
    }
    voices = entity.async_get_supported_voices("en-US")
    assert [voice.voice_id for voice in voices] == ["voice-2", "voice-1"]
    assert voices[1].name == "Home (en, fr)"


@pytest.mark.parametrize(
    ("preferred_format", "returned_format", "provider_format"),
    [
        ("ogg", "ogg", "opus"),
        ("oga", "oga", "opus"),
        ("raw", "pcm", "pcm"),
        ("wav", "wav", "wav"),
        ("unsupported", "mp3", "mp3"),
    ],
)
async def test_tts_streams_supported_audio_formats(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
    preferred_format: str,
    returned_format: str,
    provider_format: str,
) -> None:
    """Home Assistant format preferences map to Mistral speech formats."""
    _entry, client = tts_component
    client.audio.speech.complete_async.return_value = _speech_stream(
        base64.b64encode(b"mock ").decode(),
        base64.b64encode(b"audio").decode(),
    )

    result = await _entity(hass).async_get_tts_audio(
        "The front door is open.",
        "en-US",
        {
            tts.ATTR_PREFERRED_FORMAT: preferred_format,
            tts.ATTR_VOICE: "voice-2",
        },
    )

    assert result == (returned_format, b"mock audio")
    client.audio.speech.complete_async.assert_awaited_once_with(
        input="The front door is open.",
        model=DEFAULT_TTS_MODEL,
        stream=True,
        voice_id="voice-2",
        response_format=provider_format,
        timeout_ms=REQUEST_TIMEOUT_MS,
    )


async def test_tts_provider_error_is_translated(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
) -> None:
    """Speech endpoint errors use the shared runtime error mapping."""
    _entry, client = tts_component
    client.audio.speech.complete_async.side_effect = mistral_error(429, "limited")

    with pytest.raises(HomeAssistantError) as raised:
        await _entity(hass).async_get_tts_audio(
            "Hello",
            "en-US",
            {},
        )

    assert raised.value.translation_key == "api_rate_limit"


@pytest.mark.parametrize(
    ("stream", "translation_key"),
    [
        (_speech_stream("not-base64"), "tts_response_invalid"),
        (_speech_stream(), "tts_response_empty"),
    ],
)
async def test_tts_rejects_invalid_or_empty_audio(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
    stream: MagicMock,
    translation_key: str,
) -> None:
    """Malformed provider streams cannot enter Home Assistant's TTS cache."""
    _entry, client = tts_component
    client.audio.speech.complete_async.return_value = stream

    with pytest.raises(HomeAssistantError) as raised:
        await _entity(hass).async_get_tts_audio("Hello", "en-US", {})

    assert raised.value.translation_key == translation_key


async def test_tts_local_input_and_output_limits(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized text and decoded audio fail within explicit local bounds."""
    _entry, client = tts_component
    entity = _entity(hass)

    with pytest.raises(HomeAssistantError) as text_error:
        await entity.async_get_tts_audio(
            "x" * (MAX_TTS_TEXT_LENGTH + 1),
            "en-US",
            {},
        )
    assert text_error.value.translation_key == "tts_text_too_long"
    client.audio.speech.complete_async.assert_not_awaited()

    monkeypatch.setattr(
        "custom_components.mistral_conversation.tts.MAX_TTS_AUDIO_BYTES",
        2,
    )
    client.audio.speech.complete_async.return_value = _speech_stream(
        base64.b64encode(b"abc").decode()
    )
    with pytest.raises(HomeAssistantError) as audio_error:
        await entity.async_get_tts_audio("Hello", "en-US", {})
    assert audio_error.value.translation_key == "tts_audio_too_large"
