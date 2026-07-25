"""Helpers for interacting with the Mistral SDK."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from mistralai.client import Mistral
from mistralai.client.models import BaseModelCard, FTModelCard

from .const import LOGGER, SETUP_TIMEOUT_MS, MistralModel


def create_client(hass: HomeAssistant, api_key: str) -> Mistral:
    """Create a Mistral client backed by Home Assistant's shared HTTP client."""
    return Mistral(
        api_key=api_key,
        async_client=get_async_client(hass),
    )


async def async_close_client(client: Mistral) -> None:
    """Release resources owned by a Mistral client.

    The asynchronous HTTP transport is supplied by Home Assistant. The SDK
    detaches from that transport without closing Home Assistant's shared client.
    """
    with suppress(Exception):
        client.__exit__(None, None, None)
    with suppress(Exception):
        await client.__aexit__(None, None, None)


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


async def async_validate_api_key(
    hass: HomeAssistant, api_key: str
) -> list[MistralModel]:
    """Validate an API key and return available chat models."""
    client = create_client(hass, api_key)
    try:
        return await async_get_models(client)
    finally:
        await async_close_client(client)
