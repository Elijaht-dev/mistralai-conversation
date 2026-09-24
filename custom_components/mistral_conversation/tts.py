"""Text-to-speech platform for Mistral AI."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from typing import Any, override

import httpx
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.tts import (
    ATTR_PREFERRED_FORMAT,
    ATTR_VOICE,
    TextToSpeechEntity,
    TtsAudioType,
    Voice,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from mistralai.client.errors import MistralError, NoResponseError
from mistralai.client.models import SpeechOutputFormat, SpeechStreamAudioDelta
from propcache.api import cached_property

from .api import async_get_voices
from .audio import AudioNormalizer, AudioOutputTooLargeError, AudioProcessingError
from .const import (
    CONF_NORMALIZE_AUDIO,
    CONF_TARGET_LOUDNESS,
    CONF_VOICE_ID,
    DEFAULT_TARGET_LOUDNESS,
    DOMAIN,
    LOGGER,
    MAX_TTS_AUDIO_BYTES,
    MAX_TTS_TEXT_LENGTH,
    REQUEST_TIMEOUT_MS,
    SUBENTRY_TYPE_TTS,
    MistralVoice,
    validate_target_loudness,
)
from .coordinator import MistralConfigEntry
from .entity import MistralBaseEntity
from .errors import api_error_message

PARALLEL_UPDATES = 0

SUPPORTED_LANGUAGES = [
    "ar-SA",
    "de-DE",
    "en-US",
    "es-ES",
    "fr-FR",
    "hi-IN",
    "it-IT",
    "nl-NL",
    "pt-PT",
]

SUPPORTED_FORMATS: tuple[SpeechOutputFormat, ...] = (
    "mp3",
    "opus",
    "flac",
    "wav",
    "pcm",
)


def _bool_option(value: object) -> bool:
    """Parse service and media-source boolean option values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="tts_option_invalid",
    )


def _target_loudness_option(value: object) -> int:
    """Parse and validate a whole-LU target from entity or URL options."""
    try:
        return validate_target_loudness(value)
    except ValueError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="tts_option_invalid",
        ) from err


def _voice_label(voice: MistralVoice) -> str:
    """Return a concise Home Assistant label for a Mistral voice."""
    if voice.languages:
        return f"{voice.name} ({', '.join(voice.languages)})"
    return voice.name


def _audio_format(
    preferred_format: object,
) -> tuple[str, SpeechOutputFormat]:
    """Map Home Assistant's preferred format to a Mistral output format."""
    if preferred_format in ("ogg", "oga"):
        return str(preferred_format), "opus"
    if preferred_format == "raw":
        return "pcm", "pcm"
    if preferred_format in SUPPORTED_FORMATS:
        return str(preferred_format), preferred_format
    return "mp3", "mp3"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MistralConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Mistral text-to-speech entities."""
    subentries = [
        subentry
        for subentry in config_entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_TTS
    ]
    if not subentries:
        return

    try:
        voices = await async_get_voices(config_entry.runtime_data.client)
    except (
        MistralError,
        NoResponseError,
        httpx.HTTPError,
        TimeoutError,
    ) as err:
        LOGGER.warning(
            "Unable to refresh the Mistral TTS voice list: %s",
            api_error_message(err),
        )
        voices = []

    normalizer = config_entry.runtime_data.get_audio_normalizer(
        get_ffmpeg_manager(hass).binary
    )

    for subentry in subentries:
        async_add_entities(
            [MistralTTSEntity(config_entry, subentry, voices, normalizer)],
            config_subentry_id=subentry.subentry_id,
        )


class MistralTTSEntity(TextToSpeechEntity, MistralBaseEntity):
    """Generate speech with a saved Mistral voice."""

    _attr_default_language = "en-US"
    _attr_has_entity_name = False
    _attr_supported_languages = SUPPORTED_LANGUAGES

    def __init__(
        self,
        entry: MistralConfigEntry,
        subentry: ConfigSubentry,
        voices: Sequence[MistralVoice],
        normalizer: AudioNormalizer,
    ) -> None:
        """Initialize the text-to-speech entity."""
        super().__init__(entry, subentry)
        self._attr_name = subentry.title
        self._attr_supported_options = [
            ATTR_VOICE,
            ATTR_PREFERRED_FORMAT,
            CONF_NORMALIZE_AUDIO,
            CONF_TARGET_LOUDNESS,
        ]
        self._normalizer = normalizer

        configured_voice = subentry.data.get(CONF_VOICE_ID)
        available_voices = {voice.id: voice for voice in voices}
        if isinstance(configured_voice, str) and configured_voice:
            available_voices.setdefault(
                configured_voice,
                MistralVoice(id=configured_voice, name=configured_voice),
            )
        self._supported_voices = [
            Voice(voice.id, _voice_label(voice))
            for voice in sorted(
                available_voices.values(),
                key=lambda voice: (voice.name.casefold(), voice.id),
            )
        ]

    @callback
    @override
    def async_get_supported_voices(self, language: str) -> list[Voice]:
        """Return saved preset and custom voices available to this account."""
        return self._supported_voices

    @cached_property
    @override
    def default_options(self) -> Mapping[str, Any]:
        """Return the configured voice and preferred audio format."""
        options: dict[str, Any] = {
            ATTR_PREFERRED_FORMAT: "mp3",
            CONF_NORMALIZE_AUDIO: self.subentry.data.get(CONF_NORMALIZE_AUDIO, False),
            CONF_TARGET_LOUDNESS: self.subentry.data.get(
                CONF_TARGET_LOUDNESS, DEFAULT_TARGET_LOUDNESS
            ),
        }
        configured_voice = self.subentry.data.get(CONF_VOICE_ID)
        if isinstance(configured_voice, str) and configured_voice.strip():
            options[ATTR_VOICE] = configured_voice.strip()
        return options

    @override
    async def async_get_tts_audio(
        self,
        message: str,
        language: str,
        options: dict[str, Any],
    ) -> TtsAudioType:
        """Generate a bounded audio response with Mistral."""
        if len(message) > MAX_TTS_TEXT_LENGTH:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tts_text_too_long",
                translation_placeholders={"maximum": str(MAX_TTS_TEXT_LENGTH)},
            )

        voice_id = options.get(ATTR_VOICE, self.subentry.data.get(CONF_VOICE_ID))
        if not isinstance(voice_id, str) or not voice_id.strip():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tts_voice_required",
            )
        voice_id = voice_id.strip()

        response_format, provider_format = _audio_format(
            options.get(ATTR_PREFERRED_FORMAT)
        )
        normalize_audio = _bool_option(
            options.get(
                CONF_NORMALIZE_AUDIO,
                self.subentry.data.get(CONF_NORMALIZE_AUDIO, False),
            )
        )
        target_loudness = _target_loudness_option(
            options.get(
                CONF_TARGET_LOUDNESS,
                self.subentry.data.get(CONF_TARGET_LOUDNESS, DEFAULT_TARGET_LOUDNESS),
            )
        )
        response_data = bytearray()

        try:
            stream = await self.coordinator.client.audio.speech.complete_async(
                input=message,
                model=self.model,
                stream=True,
                voice_id=voice_id,
                response_format="wav" if normalize_audio else provider_format,
                timeout_ms=REQUEST_TIMEOUT_MS,
            )
            async with stream:
                async for event in stream:
                    if not isinstance(event.data, SpeechStreamAudioDelta):
                        continue
                    chunk = base64.b64decode(
                        event.data.audio_data,
                        validate=True,
                    )
                    if len(response_data) + len(chunk) > MAX_TTS_AUDIO_BYTES:
                        raise HomeAssistantError(
                            translation_domain=DOMAIN,
                            translation_key="tts_audio_too_large",
                            translation_placeholders={
                                "maximum_mb": str(MAX_TTS_AUDIO_BYTES // (1024 * 1024))
                            },
                        )
                    response_data.extend(chunk)
        except (
            MistralError,
            NoResponseError,
            httpx.HTTPError,
            TimeoutError,
        ) as err:
            await self._async_handle_api_error(err)
        except (binascii.Error, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tts_response_invalid",
            ) from err

        if not response_data:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tts_response_empty",
            )

        if normalize_audio:
            try:
                processed_audio = await self._normalizer.async_normalize(
                    bytes(response_data), provider_format, target_loudness
                )
            except AudioOutputTooLargeError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="tts_audio_too_large",
                    translation_placeholders={
                        "maximum_mb": str(MAX_TTS_AUDIO_BYTES // (1024 * 1024))
                    },
                ) from err
            except AudioProcessingError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="tts_processing_failed",
                ) from err
        else:
            processed_audio = bytes(response_data)

        self.coordinator.async_set_updated_data(self.coordinator.data or [])
        return response_format, processed_audio
