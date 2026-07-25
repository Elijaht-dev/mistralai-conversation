"""Shared fixtures for Mistral AI Conversation tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_CONVERSATION_OPTIONS,
    DOMAIN,
    MistralModel,
)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading integrations from custom_components."""


@pytest.fixture(autouse=True)
async def _setup_homeassistant(hass: HomeAssistant) -> None:
    """Set up the core Home Assistant integration."""
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def model() -> MistralModel:
    """Return model metadata that supports every conversation feature."""
    return MistralModel(
        id="mistral-small-latest",
        name="Mistral Small",
        aliases=("mistral-small-2607",),
        function_calling=True,
        reasoning=True,
        vision=True,
        ocr=True,
        max_context_length=32768,
        default_temperature=0.2,
    )


@pytest.fixture
def mock_config_entry(
    hass: HomeAssistant,
) -> MockConfigEntry:
    """Add a representative config entry to Home Assistant."""
    entry = MockConfigEntry(
        title="Mistral AI",
        domain=DOMAIN,
        data={CONF_API_KEY: "test-api-key"},
        version=1,
        minor_version=2,
        subentries_data=[
            {
                "data": DEFAULT_CONVERSATION_OPTIONS.copy(),
                "subentry_type": "conversation",
                "title": DEFAULT_CONVERSATION_NAME,
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_mistral_client() -> MagicMock:
    """Return a fully asynchronous mock of the SDK client surface used."""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.stream_async = AsyncMock()
    client.models = MagicMock()
    client.models.list_async = AsyncMock()
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_provider(
    mock_mistral_client: MagicMock,
    model: MistralModel,
) -> Generator[MagicMock]:
    """Patch coordinator client creation and model discovery."""
    with (
        patch(
            "custom_components.mistral_conversation.coordinator.create_client",
            return_value=mock_mistral_client,
        ),
        patch(
            "custom_components.mistral_conversation.coordinator.async_get_models",
            new_callable=AsyncMock,
            return_value=[model],
        ),
    ):
        yield mock_mistral_client


@pytest.fixture
async def mock_init_component(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_provider: MagicMock,
) -> AsyncGenerator[MagicMock]:
    """Initialize the integration with provider calls mocked."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    yield mock_provider


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Prevent config-flow tests from setting up newly created entries."""
    with patch(
        "custom_components.mistral_conversation.async_setup_entry",
        new_callable=AsyncMock,
        return_value=True,
    ) as setup_entry:
        yield setup_entry
