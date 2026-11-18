"""Shared language choices and resolution for Voxtral transcription."""

from typing import Final

SUPPORTED_STT_LANGUAGES: Final = (
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
)
TRANSCRIPTION_LANGUAGES: Final = (
    "pipeline",
    "auto",
    *(locale.partition("-")[0] for locale in SUPPORTED_STT_LANGUAGES),
)


def resolve_transcription_language(
    selection: object, pipeline_language: str, transport: str
) -> str | None:
    """Resolve a saved selection without changing pre-option STT behavior."""
    if selection == "legacy":
        selection = "pipeline" if transport == "batch" else "auto"
    if not isinstance(selection, str) or selection not in TRANSCRIPTION_LANGUAGES:
        raise ValueError("Invalid transcription language")
    if selection == "auto":
        return None
    if selection == "pipeline":
        language = pipeline_language.partition("-")[0].lower()
        if language not in TRANSCRIPTION_LANGUAGES[2:]:
            raise ValueError("Unsupported pipeline language")
        return language
    return selection
