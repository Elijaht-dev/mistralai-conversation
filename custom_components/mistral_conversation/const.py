"""Constants for the Mistral AI Conversation integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from homeassistant.const import CONF_LLM_HASS_API, CONF_MODEL, CONF_PROMPT
from homeassistant.helpers import llm

DOMAIN: Final = "mistral_conversation"
LOGGER = logging.getLogger(__package__)

DEFAULT_TITLE: Final = "Mistral AI"
DEFAULT_CONVERSATION_NAME: Final = "Mistral conversation"
DEFAULT_MODEL: Final = "mistral-small-latest"

CONF_MAX_TOKENS: Final = "max_tokens"
CONF_REASONING_EFFORT: Final = "reasoning_effort"
CONF_SAFE_PROMPT: Final = "safe_prompt"
CONF_TEMPERATURE: Final = "temperature"

type ReasoningEffort = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
]

REASONING_EFFORT_NONE: Final[ReasoningEffort] = "none"
REASONING_EFFORTS: Final[tuple[ReasoningEffort, ...]] = (
    REASONING_EFFORT_NONE,
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)

DEFAULT_MAX_TOKENS: Final = 2048
DEFAULT_TEMPERATURE: Final = 0.2
MAX_CONFIGURED_TOKENS: Final = 32768

DEFAULT_CONVERSATION_OPTIONS = {
    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_MODEL: DEFAULT_MODEL,
    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
    CONF_REASONING_EFFORT: REASONING_EFFORT_NONE,
    CONF_SAFE_PROMPT: False,
    CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
}

MAX_ATTACHMENT_BYTES: Final = 20 * 1024 * 1024
MAX_ATTACHMENTS: Final = 10
MAX_TOOLS: Final = 128
MAX_TOOL_ITERATIONS: Final = 10
REQUEST_TIMEOUT_MS: Final = 300_000
SETUP_TIMEOUT_MS: Final = 10_000

SUPPORTED_IMAGE_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
SUPPORTED_DOCUMENT_MIME_TYPES: Final[frozenset[str]] = frozenset({"application/pdf"})


class ApiErrorKind(StrEnum):
    """Normalized categories for errors raised by the Mistral SDK."""

    AUTHENTICATION = "authentication"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    API = "api"


@dataclass(frozen=True, slots=True)
class MistralModel:
    """Stable model metadata exposed to the integration."""

    id: str
    name: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()
    function_calling: bool = False
    reasoning: bool = False
    vision: bool = False
    ocr: bool = False
    max_context_length: int | None = None
    default_temperature: float | None = None
    deprecation: datetime | None = None
    replacement_model: str | None = None

    @property
    def label(self) -> str:
        """Return a human-readable selector label."""
        if self.name and self.name.casefold() != self.id.casefold():
            return f"{self.name} ({self.id})"
        return self.id

    @property
    def supports_documents(self) -> bool:
        """Return whether model metadata advertises document input support."""
        return self.ocr or self.vision

    def matches(self, model_id: str) -> bool:
        """Return whether an ID or alias resolves to this model."""
        folded_id = model_id.casefold()
        return self.id.casefold() == folded_id or any(
            alias.casefold() == folded_id for alias in self.aliases
        )
