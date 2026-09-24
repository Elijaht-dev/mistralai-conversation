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

from custom_components.mistral_conversation.audio import AudioProcessingError
from custom_components.mistral_conversation.const import (
    CONF_NORMALIZE_AUDIO,
    CONF_TARGET_LOUDNESS,
    CONF_VOICE_ID,
    DEFAULT_TARGET_LOUDNESS,
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
    assert entity.supported_options == [
        tts.ATTR_VOICE,
        tts.ATTR_PREFERRED_FORMAT,
        CONF_NORMALIZE_AUDIO,
        CONF_TARGET_LOUDNESS,
    ]
    assert entity.default_options == {
        tts.ATTR_VOICE: "voice-1",
        tts.ATTR_PREFERRED_FORMAT: "mp3",
        CONF_NORMALIZE_AUDIO: False,
        CONF_TARGET_LOUDNESS: DEFAULT_TARGET_LOUDNESS,
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


@pytest.mark.parametrize(
    ("normalize_option", "target_option", "requested_target"),
    [
        (True, -18, -18),
        ("true", "-20", -20),
    ],
)
async def test_tts_normalizes_with_per_call_options(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
    normalize_option: object,
    target_option: object,
    requested_target: int,
) -> None:
    """Service and media-source option types request WAV and local processing."""
    _entry, client = tts_component
    source = b"provider WAV"
    client.audio.speech.complete_async.return_value = _speech_stream(
        base64.b64encode(source).decode()
    )
    entity = _entity(hass)
    with patch.object(
        entity._normalizer,
        "async_normalize",
        new_callable=AsyncMock,
        return_value=b"processed mp3",
    ) as normalize:
        result = await entity.async_get_tts_audio(
            "Hello",
            "en-US",
            {
                tts.ATTR_PREFERRED_FORMAT: "mp3",
                CONF_NORMALIZE_AUDIO: normalize_option,
                CONF_TARGET_LOUDNESS: target_option,
            },
        )

    assert result == ("mp3", b"processed mp3")
    normalize.assert_awaited_once_with(source, "mp3", requested_target)
    assert (
        client.audio.speech.complete_async.await_args.kwargs["response_format"] == "wav"
    )


async def test_tts_media_source_options_and_cache(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
) -> None:
    """A TTS media-source URL applies string options and separates cache entries."""
    _entry, client = tts_component
    source = b"provider audio"
    client.audio.speech.complete_async.return_value = _speech_stream(
        base64.b64encode(source).decode()
    )
    entity = _entity(hass)
    with patch.object(
        entity._normalizer,
        "async_normalize",
        new_callable=AsyncMock,
        return_value=b"processed opus",
    ) as normalize:
        original = await tts.async_get_media_source_audio(
            hass,
            f"{ENTITY_ID}?message=Cache+isolation&preferred_format=ogg&"
            "normalize_audio=false&cache=false",
        )
        processed_url = (
            f"{ENTITY_ID}?message=Cache+isolation&preferred_format=ogg&"
            "normalize_audio=true&target_loudness=-18&cache=false"
        )
        processed = await tts.async_get_media_source_audio(hass, processed_url)
        cached = await tts.async_get_media_source_audio(hass, processed_url)
        await tts.async_get_media_source_audio(
            hass, processed_url.replace("target_loudness=-18", "target_loudness=-20")
        )

    assert original == ("ogg", source)
    assert processed == cached == ("ogg", b"processed opus")
    assert normalize.await_count == 2
    assert [call.args for call in normalize.await_args_list] == [
        (source, "opus", -18),
        (source, "opus", -20),
    ]
    assert client.audio.speech.complete_async.await_count == 3


@pytest.mark.parametrize(
    "options",
    [
        {CONF_NORMALIZE_AUDIO: "false", CONF_TARGET_LOUDNESS: "-18"},
        {CONF_TARGET_LOUDNESS: "-18"},
    ],
)
async def test_tts_disabled_override_keeps_original_audio(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
    options: dict[str, str],
) -> None:
    """A target alone or a false string keeps provider bytes untouched."""
    _entry, client = tts_component
    source = b"provider bytes"
    client.audio.speech.complete_async.return_value = _speech_stream(
        base64.b64encode(source).decode()
    )
    entity = _entity(hass)
    with patch.object(
        entity._normalizer, "async_normalize", new_callable=AsyncMock
    ) as normalize:
        assert await entity.async_get_tts_audio("Hello", "en-US", options) == (
            "mp3",
            source,
        )
    normalize.assert_not_awaited()
    assert (
        client.audio.speech.complete_async.await_args.kwargs["response_format"] == "mp3"
    )


@pytest.mark.parametrize(
    "options",
    [
        {CONF_NORMALIZE_AUDIO: "invalid"},
        {CONF_TARGET_LOUDNESS: -25},
        {CONF_TARGET_LOUDNESS: "-16.5"},
        {CONF_TARGET_LOUDNESS: "nan"},
        {CONF_TARGET_LOUDNESS: 10**1000},
    ],
)
async def test_tts_rejects_invalid_normalization_options_before_provider_call(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
    options: dict[str, object],
) -> None:
    """Invalid service or URL options fail locally and never reach Mistral."""
    _entry, client = tts_component
    with pytest.raises(HomeAssistantError) as raised:
        await _entity(hass).async_get_tts_audio("Hello", "en-US", options)
    assert raised.value.translation_key == "tts_option_invalid"
    client.audio.speech.complete_async.assert_not_awaited()


async def test_tts_processing_failure_is_local(
    hass: HomeAssistant,
    tts_component: tuple[MockConfigEntry, MagicMock],
) -> None:
    """FFmpeg failures are translated without marking the provider unavailable."""
    _entry, client = tts_component
    client.audio.speech.complete_async.return_value = _speech_stream(
        base64.b64encode(b"source").decode()
    )
    entity = _entity(hass)
    with (
        patch.object(
            entity._normalizer,
            "async_normalize",
            new_callable=AsyncMock,
            side_effect=AudioProcessingError("ffmpeg failed"),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_get_tts_audio("Hello", "en-US", {CONF_NORMALIZE_AUDIO: True})
    assert raised.value.translation_key == "tts_processing_failed"
    assert entity.available


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
