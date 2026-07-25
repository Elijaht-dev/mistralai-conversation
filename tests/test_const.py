"""Tests for Mistral integration constants and model metadata."""

from __future__ import annotations

from custom_components.mistral_conversation.const import MistralModel


def test_model_label_prefers_human_name() -> None:
    """A distinct provider display name is included in the selector label."""
    model = MistralModel(id="model-id", name="Model Name")

    assert model.label == "Model Name (model-id)"


def test_model_label_does_not_duplicate_id() -> None:
    """A case-only name variation is not displayed twice."""
    model = MistralModel(id="Model-ID", name="model-id")

    assert model.label == "Model-ID"


def test_model_matches_id_and_alias_case_insensitively() -> None:
    """Exact IDs and provider aliases resolve without case sensitivity."""
    model = MistralModel(id="mistral-small", aliases=("small-latest",))

    assert model.matches("MISTRAL-SMALL")
    assert model.matches("SMALL-LATEST")
    assert not model.matches("mistral-large")


def test_document_support_uses_vision_or_ocr() -> None:
    """Either advertised multimodal capability permits PDF input."""
    assert MistralModel(id="vision", vision=True).supports_documents
    assert MistralModel(id="ocr", ocr=True).supports_documents
    assert not MistralModel(id="text").supports_documents
