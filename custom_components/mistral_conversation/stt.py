"""Speech-to-text platform for Mistral AI."""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterable
from typing import override

import httpx
from homeassistant.components import stt
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from mistralai.client.errors import MistralError, NoResponseError

from .const import (
    LOGGER,
    MAX_STT_AUDIO_BYTES,
    REQUEST_TIMEOUT_MS,
    SUBENTRY_TYPE_STT,
)
from .coordinator import MistralConfigEntry
from .entity import MistralBaseEntity

PARALLEL_UPDATES = 0

SUPPORTED_LANGUAGES = [
    "ar-SA",
    "de-DE",
    "en-US",
    "es-ES",
    "fr-FR",
    "hi-IN",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "nl-NL",
    "pt-PT",
    "ru-RU",
    "zh-CN",
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MistralConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Mistral speech-to-text entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_STT:
            continue
        async_add_entities(
            [MistralSTTEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class MistralSTTEntity(stt.SpeechToTextEntity, MistralBaseEntity):
    """Transcribe Home Assistant voice audio with Mistral."""

    _attr_translation_key = "stt"

    @property
    @override
    def supported_languages(self) -> list[str]:
        """Return languages supported by Voxtral transcription."""
        return SUPPORTED_LANGUAGES

    @property
    @override
    def supported_formats(self) -> list[stt.AudioFormats]:
        """Return supported audio containers."""
        return [stt.AudioFormats.WAV]

    @property
    @override
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        """Return supported audio codecs."""
        return [stt.AudioCodecs.PCM]

    @property
    @override
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        """Return supported PCM bit depths."""
        return [stt.AudioBitRates.BITRATE_16]

    @property
    @override
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        """Return supported sample rates."""
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    @override
    def supported_channels(self) -> list[stt.AudioChannels]:
        """Return supported channel counts."""
        return [stt.AudioChannels.CHANNEL_MONO]

    @override
    async def async_process_audio_stream(
        self,
        metadata: stt.SpeechMetadata,
        stream: AsyncIterable[bytes],
    ) -> stt.SpeechResult:
        """Buffer a bounded Assist stream and transcribe it with Voxtral."""
        audio_bytes = bytearray()
        async for chunk in stream:
            if len(audio_bytes) + len(chunk) > MAX_STT_AUDIO_BYTES:
                LOGGER.warning("Mistral STT audio exceeded the local size limit")
                return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
            audio_bytes.extend(chunk)

        if not audio_bytes:
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(metadata.channel.value)
            wav_file.setsampwidth(metadata.bit_rate.value // 8)
            wav_file.setframerate(metadata.sample_rate.value)
            wav_file.writeframes(audio_bytes)

        try:
            response = (
                await self.coordinator.client.audio.transcriptions.complete_async(
                    model=self.model,
                    file={
                        "file_name": "home-assistant-voice.wav",
                        "content": wav_buffer.getvalue(),
                        "content_type": "audio/wav",
                    },
                    language=metadata.language.partition("-")[0],
                    timeout_ms=REQUEST_TIMEOUT_MS,
                )
            )
        except (
            MistralError,
            NoResponseError,
            httpx.HTTPError,
            TimeoutError,
        ) as err:
            translation_key, message = await self._async_process_api_error(err)
            LOGGER.warning("Mistral STT failed (%s): %s", translation_key, message)
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)

        if not response.text:
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)

        self.coordinator.async_set_updated_data(self.coordinator.data or [])
        return stt.SpeechResult(response.text, stt.SpeechResultState.SUCCESS)
