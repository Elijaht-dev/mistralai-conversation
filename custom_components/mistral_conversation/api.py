"""Helpers for interacting with the Mistral SDK."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from types import TracebackType
from typing import Protocol, cast

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from mistralai.client import Mistral, errors, models, utils

# Load lazy SDK services through HA's integration import executor, before setup.
from mistralai.client.audio import Audio  # noqa: F401
from mistralai.client.chat import Chat  # noqa: F401
from mistralai.client.models import BaseModelCard, FTModelCard
from mistralai.client.models_ import Models  # noqa: F401

from .const import (
    LOGGER,
    MAX_VOICES,
    SETUP_TIMEOUT_MS,
    VOICE_LIST_PAGE_SIZE,
    MistralModel,
    MistralVoice,
)

# SDK 2.10.0's lazy exports do not cache resolved attributes. Merely importing
# their modules still calls import_module (with relative names) on every access.
# Bind the original public objects once while HA imports this integration in its
# import executor. No SDK function is replaced and no blocking warning is muted.
# Limit model loading to the request/response types used by our five endpoints.
for _name in (
    "Security",
    "ListModelsV1ModelsGetRequest",
    "ModelList",
    "ChatCompletionStreamRequest",
    "ChatCompletionStreamRequestStop",
    "ChatCompletionStreamRequestMessage",
    "ChatCompletionStreamRequestTool",
    "ChatCompletionStreamRequestToolChoice",
    "ResponseFormat",
    "Prediction",
    "GuardrailConfig",
    "CompletionEvent",
    "AudioTranscriptionRequest",
    "File",
    "TimestampGranularity",
    "TranscriptionResponse",
    "SpeechRequest",
    "SpeechResponse",
    "SpeechStreamEvents",
    "ListVoicesV1AudioVoicesGetRequest",
    "VoiceListResponse",
):
    setattr(models, _name, getattr(models, _name))
for _module in (errors, utils):
    for _name in _module.__all__:
        setattr(_module, _name, getattr(_module, _name))


class _ClosableMistralClient(Protocol):
    """Typed context-manager surface missing from the generated SDK hints."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> object:
        """Close synchronous SDK-owned resources."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> object:
        """Close asynchronous SDK-owned resources."""
        ...


def _create_client(api_key: str, http_client: httpx.AsyncClient) -> Mistral:
    """Construct and prime the SDK in an executor without making requests."""
    client = Mistral(
        api_key=api_key,
        async_client=http_client,
    )
    try:
        # Mistral also creates a sync HTTP client and lazily initializes services.
        # Do both here, including the audio service's speech/transcription/voices.
        _ = client.models, client.chat, client.audio
    except BaseException:
        with suppress(Exception):
            cast(_ClosableMistralClient, client).__exit__(None, None, None)
        raise
    return client


async def async_create_client(hass: HomeAssistant, api_key: str) -> Mistral:
    """Keep HA transport access on the loop and blocking SDK setup off it."""
    future = hass.async_add_executor_job(
        _create_client, api_key, get_async_client(hass)
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Cancelling the await cannot stop the worker; reclaim its eventual client.
        with suppress(Exception):
            client = await future
            await async_close_client(hass, client)
        raise


async def async_close_client(hass: HomeAssistant, client: Mistral) -> None:
    """Release resources owned by a Mistral client.

    The asynchronous HTTP transport is supplied by Home Assistant. The SDK
    detaches from that transport without closing Home Assistant's shared client.
    """
    closable_client = cast(_ClosableMistralClient, client)
    with suppress(Exception):
        await hass.async_add_executor_job(closable_client.__exit__, None, None, None)
    with suppress(Exception):
        await closable_client.__aexit__(None, None, None)


def _optional_string(value: object) -> str | None:
    """Return a non-empty SDK string value, ignoring its UNSET sentinel."""
    return value if isinstance(value, str) and value else None


def _optional_datetime(value: object) -> datetime | None:
    """Return an SDK datetime value, ignoring its UNSET sentinel."""
    return value if isinstance(value, datetime) else None


def _parse_model(model: BaseModelCard | FTModelCard) -> MistralModel | None:
    """Convert a generated SDK model card into stable integration metadata."""
    if isinstance(model, FTModelCard) and model.archived:
        return None

    capabilities = model.capabilities
    if not capabilities.completion_chat:
        return None

    return MistralModel(
        id=model.id,
        name=_optional_string(model.name),
        description=_optional_string(model.description),
        aliases=tuple(alias for alias in model.aliases or [] if alias),
        function_calling=bool(capabilities.function_calling),
        reasoning=bool(capabilities.reasoning),
        vision=bool(capabilities.vision),
        ocr=bool(capabilities.ocr),
        max_context_length=model.max_context_length,
        default_temperature=(
            model.default_model_temperature
            if isinstance(model.default_model_temperature, float | int)
            else None
        ),
        deprecation=_optional_datetime(model.deprecation),
        replacement_model=_optional_string(model.deprecation_replacement_model),
    )


async def async_get_models(client: Mistral) -> list[MistralModel]:
    """Return chat-capable, non-archived models available to the account."""
    response = await client.models.list_async(timeout_ms=SETUP_TIMEOUT_MS)
    models: list[MistralModel] = []

    for provider_model in response.data or []:
        if not isinstance(provider_model, BaseModelCard | FTModelCard):
            LOGGER.debug(
                "Ignoring an unsupported Mistral model-card variant: %s",
                type(provider_model).__name__,
            )
            continue
        if (model := _parse_model(provider_model)) is not None:
            models.append(model)

    return sorted(models, key=lambda model: model.id.casefold())


async def async_get_voices(client: Mistral) -> list[MistralVoice]:
    """Return preset and custom TTS voices available to the account."""
    voices: list[MistralVoice] = []
    offset = 0

    while offset < MAX_VOICES:
        page_size = min(VOICE_LIST_PAGE_SIZE, MAX_VOICES - offset)
        response = await client.audio.voices.list_async(
            limit=page_size,
            offset=offset,
            type_="all",
            timeout_ms=SETUP_TIMEOUT_MS,
        )
        if not response.items:
            break

        voices.extend(
            MistralVoice(
                id=voice.id,
                name=voice.name,
                languages=tuple(voice.languages or []),
            )
            for voice in response.items
            if voice.id and voice.name
        )
        offset += len(response.items)
        if offset >= response.total:
            break

    return sorted(voices, key=lambda voice: (voice.name.casefold(), voice.id))


async def async_validate_api_key(
    hass: HomeAssistant, api_key: str
) -> list[MistralModel]:
    """Validate an API key and return available chat models."""
    client = await async_create_client(hass, api_key)
    try:
        return await async_get_models(client)
    finally:
        await async_close_client(hass, client)
