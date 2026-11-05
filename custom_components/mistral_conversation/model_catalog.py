"""Curated model choices and endpoint selection, independent of SDK objects."""

from typing import Literal

from .const import LEGACY_REALTIME_STT_MODEL, REALTIME_STT_MODEL, MistralModel

type TranscriptionMode = Literal["auto", "batch", "realtime"]
type TranscriptionTransport = Literal["batch", "realtime"]
type ModelUsage = Literal["chat", "image", "stt", "tts"]

MODEL_PRESETS: dict[ModelUsage, tuple[tuple[str, str], ...]] = {
    "chat": (
        ("mistral-small-latest", "Mistral Small 4"),
        ("mistral-large-4", "Mistral Large 4"),
        ("ministral-3b-2512", "Ministral 3 3B"),
        ("ministral-8b-2512", "Ministral 3 8B"),
        ("ministral-14b-2512", "Ministral 3 14B"),
    ),
    "image": (
        ("mistral-large-4", "Mistral Large 4"),
        ("mistral-small-latest", "Mistral Small 4"),
    ),
    "stt": (
        (REALTIME_STT_MODEL, "Voxtral Mini Transcribe Realtime 3"),
        ("voxtral-mini-transcribe-3-0", "Voxtral Mini Transcribe 3"),
        ("voxtral-small-transcribe-3", "Voxtral Small Transcribe 3"),
    ),
    "tts": (("voxtral-mini-tts-3-0", "Voxtral TTS 3"),),
}

_REALTIME_MODELS = frozenset(
    {
        LEGACY_REALTIME_STT_MODEL,
        REALTIME_STT_MODEL,
        "voxtral-mini-transcribe-realtime-3",
        "voxtral-mini-transcribe-realtime-latest",
    }
)


def resolve_transcription_transport(
    model: MistralModel, known: bool, mode: str
) -> TranscriptionTransport:
    """Respect discovered capabilities before documented IDs and manual choices."""
    if mode not in ("auto", "batch", "realtime"):
        raise ValueError("Invalid transcription mode")
    if mode == "auto":
        if known:
            if model.audio_transcription_realtime:
                return "realtime"
            if model.audio_transcription:
                return "batch"
            raise ValueError("Model does not support transcription")
        return "realtime" if model.id.casefold() in _REALTIME_MODELS else "batch"
    if known and not (
        model.audio_transcription_realtime
        if mode == "realtime"
        else model.audio_transcription
    ):
        raise ValueError("Model does not support the selected transcription mode")
    return "realtime" if mode == "realtime" else "batch"
