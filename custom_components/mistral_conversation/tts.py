"""Text-to-speech platform for Mistral AI."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, override

import httpx2
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.tts import (
    ATTR_PREFERRED_FORMAT,
    ATTR_VOICE,
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
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

from .api import async_get_voices, async_stream_speech
from .audio import AudioNormalizer, AudioOutputTooLargeError, AudioProcessingError
from .const import (
    CONF_NORMALIZE_AUDIO,
    CONF_SYNTHESIS_MODE,
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
from .errors import RealtimeError, SpeechStreamError, api_error_message
from .model_catalog import speech_model_error

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


@dataclass(frozen=True, slots=True)
class _SpeechOptions:
    """Validated options shared by buffered and realtime requests."""

    extension: str
    output_format: SpeechOutputFormat
    voice_id: str
    normalize: bool
    target_loudness: int


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
        httpx2.HTTPError,
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
        self._realtime_streams: set[AsyncGenerator[bytes]] = set()
        self._realtime_tasks: set[asyncio.Task[bytes]] = set()
        self._removing = False

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
    def async_supports_streaming_input(self) -> bool:
        """Only advertise incremental input for the explicitly selected mode."""
        mode = self.subentry.data.get(CONF_SYNTHESIS_MODE, "standard")
        return isinstance(mode, str) and mode == "realtime"

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Reap requests even while their audio consumer is paused."""
        self._removing = True
        tasks = tuple(self._realtime_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(stream.aclose() for stream in tuple(self._realtime_streams)),
            return_exceptions=True,
        )
        await super().async_will_remove_from_hass()

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

        speech = self._speech_options(options)
        if self.async_supports_streaming_input():

            async def text() -> AsyncGenerator[str]:
                yield message

            return speech.extension, b"".join(
                [chunk async for chunk in self._stream_realtime_audio(text(), speech)]
            )
        return await self._async_standard_audio(message, speech)

    @override
    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        """Bridge HA's incremental text and audio interface to Mistral."""
        speech = self._speech_options(request.options)
        if self.async_supports_streaming_input():
            return TTSAudioResponse(
                speech.extension,
                self._stream_realtime_audio(request.message_gen, speech),
            )
        # Also bound direct calls to the streaming method in Standard mode.
        chunks: list[str] = []
        size = 0
        async for delta in request.message_gen:
            size += len(delta)
            if size > MAX_TTS_TEXT_LENGTH:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="tts_text_too_long",
                    translation_placeholders={"maximum": str(MAX_TTS_TEXT_LENGTH)},
                )
            chunks.append(delta)
        extension, data = await self._async_standard_audio("".join(chunks), speech)

        async def audio() -> AsyncGenerator[bytes]:
            yield data

        return TTSAudioResponse(extension, audio())

    async def _stream_realtime_audio(
        self, text: AsyncGenerator[str], speech: _SpeechOptions
    ) -> AsyncGenerator[bytes]:
        """Track the provider generator across slow consumers and entity unload."""
        if self._removing:
            await text.aclose()
            raise asyncio.CancelledError
        stream = async_stream_speech(
            self.coordinator.client,
            self.model,
            speech.voice_id,
            speech.output_format,
            text,
        )
        self._realtime_streams.add(stream)
        try:
            while True:
                if self._removing:
                    raise asyncio.CancelledError
                task = asyncio.create_task(anext(stream))
                self._realtime_tasks.add(task)
                try:
                    chunk = await task
                except StopAsyncIteration:
                    break
                finally:
                    self._realtime_tasks.discard(task)
                yield chunk
        except SpeechStreamError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=err.translation_key,
                translation_placeholders={
                    "maximum": str(MAX_TTS_TEXT_LENGTH),
                    "maximum_mb": str(MAX_TTS_AUDIO_BYTES // (1024 * 1024)),
                },
            ) from None
        except RealtimeError as err:
            await self._async_handle_api_error(err)
        finally:
            await stream.aclose()
            self._realtime_streams.discard(stream)
        self.coordinator.async_set_updated_data(self.coordinator.data or [])

    def _speech_options(self, options: dict[str, Any]) -> _SpeechOptions:
        """Validate voice, mode, normalization and discovered capabilities locally."""
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
        mode = self.subentry.data.get(CONF_SYNTHESIS_MODE, "standard")
        if mode == "realtime" and normalize_audio:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="tts_realtime_normalization",
            )
        self._validate_speech_model(mode, "wav" if normalize_audio else provider_format)
        return _SpeechOptions(
            response_format, provider_format, voice_id, normalize_audio, target_loudness
        )

    async def _async_standard_audio(
        self, message: str, speech: _SpeechOptions
    ) -> tuple[str, bytes]:
        """Keep the original HTTP generation and whole-clip normalization path."""
        response_data = bytearray()

        try:
            stream = await self.coordinator.client.audio.speech.complete_async(
                input=message,
                model=self.model,
                stream=True,
                voice_id=speech.voice_id,
                response_format="wav" if speech.normalize else speech.output_format,
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
            httpx2.HTTPError,
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

        if speech.normalize:
            try:
                processed_audio = await self._normalizer.async_normalize(
                    bytes(response_data), speech.output_format, speech.target_loudness
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
        return speech.extension, processed_audio

    def _validate_speech_model(self, mode: str, output_format: str) -> None:
        """Reject known incompatible models before sending any text to Mistral."""
        model, known = self.coordinator.get_model_info(self.model)
        if error := speech_model_error(model, known, mode, output_format):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=error,
                translation_placeholders={"model": self.model, "format": output_format},
            )
