"""Model endpoint decisions with discovered, legacy and custom identifiers."""

from dataclasses import replace

import pytest

from custom_components.mistral_conversation.const import (
    LEGACY_REALTIME_STT_MODEL,
    REALTIME_STT_MODEL,
    MistralModel,
)
from custom_components.mistral_conversation.model_catalog import (
    resolve_transcription_transport,
)


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        (REALTIME_STT_MODEL, "realtime"),
        (LEGACY_REALTIME_STT_MODEL, "realtime"),
        ("voxtral-mini-transcribe-realtime-3", "realtime"),
        ("voxtral-mini-transcribe-realtime-latest", "realtime"),
        ("VOXTRAL-MINI-TRANSCRIBE-REALTIME-3", "realtime"),
        ("voxtral-mini-transcribe-3-0", "batch"),
        ("voxtral-small-transcribe-3", "batch"),
        ("voxtral-mini-latest", "batch"),
        ("custom-realtime-model", "batch"),
        ("ft:custom-model", "batch"),
    ],
)
def test_automatic_undiscovered_model_transport(model_id, expected):
    """Only documented Realtime IDs change the legacy batch fallback."""
    assert (
        resolve_transcription_transport(MistralModel(id=model_id), False, "auto")
        == expected
    )


@pytest.mark.parametrize("mode", ["batch", "realtime"])
def test_custom_model_can_select_transport(mode):
    """A custom ID does not have to follow Mistral's naming convention."""
    assert (
        resolve_transcription_transport(MistralModel(id="ft:voice"), False, mode)
        == mode
    )


@pytest.mark.parametrize(
    ("batch", "realtime", "mode", "expected"),
    [
        (True, False, "auto", "batch"),
        (False, True, "auto", "realtime"),
        (True, True, "auto", "realtime"),
        (True, True, "batch", "batch"),
        (True, True, "realtime", "realtime"),
    ],
)
def test_discovered_capabilities_take_precedence(batch, realtime, mode, expected):
    """Provider capabilities override even a documented ID's transport hint."""
    model = replace(
        MistralModel(id=REALTIME_STT_MODEL),
        audio_transcription=batch,
        audio_transcription_realtime=realtime,
    )
    assert resolve_transcription_transport(model, True, mode) == expected


@pytest.mark.parametrize(
    ("batch", "realtime", "mode"),
    [
        (False, False, "auto"),
        (True, False, "realtime"),
        (False, True, "batch"),
        (False, False, "batch"),
        (True, True, "invalid"),
    ],
)
def test_known_incompatible_models_and_invalid_modes_fail_closed(batch, realtime, mode):
    model = MistralModel(
        id="known", audio_transcription=batch, audio_transcription_realtime=realtime
    )
    with pytest.raises(ValueError):
        resolve_transcription_transport(model, True, mode)
