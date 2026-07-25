"""Tests for the typed Mistral SDK boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from mistralai.client.models import BaseModelCard, FTModelCard, ModelCapabilities

from custom_components.mistral_conversation.api import (
    async_close_client,
    async_get_models,
    async_validate_api_key,
    create_client,
)
from custom_components.mistral_conversation.const import SETUP_TIMEOUT_MS


def _model_card(
    model_id: str,
    *,
    chat: bool = True,
    function_calling: bool = False,
    reasoning: bool = False,
    vision: bool = False,
    ocr: bool = False,
) -> BaseModelCard:
    """Build a provider model card."""
    return BaseModelCard(
        id=model_id,
        capabilities=ModelCapabilities(
            completion_chat=chat,
            function_calling=function_calling,
            reasoning=reasoning,
            vision=vision,
            ocr=ocr,
        ),
    )


async def test_get_models_parses_metadata_and_sorts() -> None:
    """Model cards are filtered and converted into stable metadata."""
    deprecation = datetime(2027, 1, 2, tzinfo=UTC)
    rich_model = BaseModelCard(
        id="z-model",
        name="Zed",
        description="A capable model",
        aliases=["z-latest"],
        max_context_length=131072,
        default_model_temperature=0.35,
        deprecation=deprecation,
        deprecation_replacement_model="next-model",
        capabilities=ModelCapabilities(
            completion_chat=True,
            function_calling=True,
            reasoning=True,
            vision=True,
            ocr=True,
        ),
    )
    client = MagicMock()
    client.models.list_async = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                rich_model,
                _model_card("not-chat", chat=False),
                _model_card("a-model"),
                object(),
            ]
        )
    )

    models = await async_get_models(client)

    assert [model.id for model in models] == ["a-model", "z-model"]
    model = models[1]
    assert model.name == "Zed"
    assert model.description == "A capable model"
    assert model.aliases == ("z-latest",)
    assert model.max_context_length == 131072
    assert model.default_temperature == 0.35
    assert model.deprecation == deprecation
    assert model.replacement_model == "next-model"
    assert model.function_calling
    assert model.reasoning
    assert model.vision
    assert model.ocr
    client.models.list_async.assert_awaited_once_with(timeout_ms=SETUP_TIMEOUT_MS)


async def test_get_models_filters_archived_fine_tunes() -> None:
    """Archived fine-tuned models are not offered to new agents."""
    client = MagicMock()
    client.models.list_async = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                FTModelCard(
                    id="archived",
                    job="job-id",
                    root="root-model",
                    archived=True,
                    capabilities=ModelCapabilities(completion_chat=True),
                ),
                FTModelCard(
                    id="active",
                    job="job-id",
                    root="root-model",
                    archived=False,
                    capabilities=ModelCapabilities(completion_chat=True),
                ),
            ]
        )
    )

    models = await async_get_models(client)

    assert [model.id for model in models] == ["active"]


def test_create_client_uses_home_assistant_http_client(
    hass: HomeAssistant,
) -> None:
    """The SDK shares Home Assistant's managed asynchronous transport."""
    shared_client = object()
    sdk_client = object()
    with (
        patch(
            "custom_components.mistral_conversation.api.get_async_client",
            return_value=shared_client,
        ),
        patch(
            "custom_components.mistral_conversation.api.Mistral",
            return_value=sdk_client,
        ) as mistral,
    ):
        result = create_client(hass, "secret")

    assert result is sdk_client
    mistral.assert_called_once_with(
        api_key="secret",
        async_client=shared_client,
    )


async def test_validate_api_key_closes_client(
    hass: HomeAssistant,
) -> None:
    """Temporary validation clients are closed after a successful request."""
    client = MagicMock()
    models = []
    with (
        patch(
            "custom_components.mistral_conversation.api.create_client",
            return_value=client,
        ),
        patch(
            "custom_components.mistral_conversation.api.async_get_models",
            new_callable=AsyncMock,
            return_value=models,
        ),
        patch(
            "custom_components.mistral_conversation.api.async_close_client",
            new_callable=AsyncMock,
        ) as close_client,
    ):
        result = await async_validate_api_key(hass, "secret")

    assert result is models
    close_client.assert_awaited_once_with(client)


async def test_validate_api_key_closes_client_on_error(
    hass: HomeAssistant,
) -> None:
    """Temporary validation clients are also closed after provider errors."""
    client = MagicMock()
    with (
        patch(
            "custom_components.mistral_conversation.api.create_client",
            return_value=client,
        ),
        patch(
            "custom_components.mistral_conversation.api.async_get_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "custom_components.mistral_conversation.api.async_close_client",
            new_callable=AsyncMock,
        ) as close_client,
        pytest.raises(RuntimeError, match="boom"),
    ):
        await async_validate_api_key(hass, "secret")

    close_client.assert_awaited_once_with(client)


async def test_close_client_suppresses_cleanup_errors() -> None:
    """Cleanup cannot mask the original setup or unload failure."""
    client = MagicMock()
    client.__exit__.side_effect = RuntimeError("sync close")
    client.__aexit__ = AsyncMock(side_effect=RuntimeError("async close"))

    await async_close_client(client)

    client.__exit__.assert_called_once_with(None, None, None)
    client.__aexit__.assert_awaited_once_with(None, None, None)
