"""Tests for the Mistral model coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import MistralModel
from custom_components.mistral_conversation.coordinator import (
    UPDATE_INTERVAL_CONNECTED,
    UPDATE_INTERVAL_DISCONNECTED,
    MistralCoordinator,
)

from .helpers import mistral_error


async def test_refresh_creates_client_and_loads_models(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mistral_client: MagicMock,
    model: MistralModel,
) -> None:
    """A coordinator refresh initializes the shared SDK client."""
    coordinator = MistralCoordinator(hass, mock_config_entry)
    with (
        patch(
            "custom_components.mistral_conversation.coordinator.create_client",
            return_value=mock_mistral_client,
        ) as create_client,
        patch(
            "custom_components.mistral_conversation.coordinator.async_get_models",
            new_callable=AsyncMock,
            return_value=[model],
        ) as get_models,
    ):
        await coordinator.async_refresh()

    assert coordinator.client is mock_mistral_client
    assert coordinator.data == [model]
    assert coordinator.last_update_success
    assert coordinator.update_interval == UPDATE_INTERVAL_CONNECTED
    create_client.assert_called_once_with(hass, "test-api-key")
    get_models.assert_awaited_once_with(mock_mistral_client)


@pytest.mark.parametrize("status_code", [401, 403])
async def test_authentication_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mistral_client: MagicMock,
    status_code: int,
) -> None:
    """Rejected credentials become Home Assistant auth failures."""
    coordinator = MistralCoordinator(hass, mock_config_entry)
    coordinator._client = mock_mistral_client
    with (
        patch(
            "custom_components.mistral_conversation.coordinator.async_get_models",
            new_callable=AsyncMock,
            side_effect=mistral_error(status_code, "bad key"),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await coordinator.async_update_data()

    assert coordinator.update_interval == UPDATE_INTERVAL_DISCONNECTED


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("slow"),
        httpx.ReadTimeout(
            "slow",
            request=httpx.Request("GET", "https://api.mistral.ai"),
        ),
    ],
)
async def test_timeout_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mistral_client: MagicMock,
    error: BaseException,
) -> None:
    """Timeout failures retain timeout semantics for coordinator retry handling."""
    coordinator = MistralCoordinator(hass, mock_config_entry)
    coordinator._client = mock_mistral_client
    with (
        patch(
            "custom_components.mistral_conversation.coordinator.async_get_models",
            new_callable=AsyncMock,
            side_effect=error,
        ),
        pytest.raises(TimeoutError),
    ):
        await coordinator.async_update_data()


@pytest.mark.parametrize(
    "error",
    [
        mistral_error(400, "bad request"),
        mistral_error(500, "server unavailable"),
        httpx.ConnectError(
            "offline",
            request=httpx.Request("GET", "https://api.mistral.ai"),
        ),
    ],
)
async def test_update_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mistral_client: MagicMock,
    error: BaseException,
) -> None:
    """Other expected provider failures become translated update failures."""
    coordinator = MistralCoordinator(hass, mock_config_entry)
    coordinator._client = mock_mistral_client
    with (
        patch(
            "custom_components.mistral_conversation.coordinator.async_get_models",
            new_callable=AsyncMock,
            side_effect=error,
        ),
        pytest.raises(UpdateFailed),
    ):
        await coordinator.async_update_data()


def test_get_model_info_exact_alias_and_unknown(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    model: MistralModel,
) -> None:
    """Cached metadata resolves exact IDs and aliases with a safe fallback."""
    coordinator = MistralCoordinator(hass, mock_config_entry)
    coordinator.async_set_updated_data([model])

    assert coordinator.get_model_info(model.id) == (model, True)
    assert coordinator.get_model_info("MISTRAL-SMALL-2607") == (model, True)
    unknown, known = coordinator.get_model_info("fine-tuned/custom")
    assert not known
    assert unknown.id == "fine-tuned/custom"
    assert not unknown.function_calling


def test_mark_connection_error_and_restore(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    model: MistralModel,
) -> None:
    """Runtime transport failures make coordinator entities unavailable."""
    coordinator = MistralCoordinator(hass, mock_config_entry)
    coordinator.async_set_updated_data([model])
    remove_listener = coordinator.async_add_listener(MagicMock())

    with patch.object(coordinator, "_schedule_refresh") as schedule_refresh:
        coordinator.mark_connection_error()

    assert not coordinator.last_update_success
    assert coordinator.update_interval == UPDATE_INTERVAL_DISCONNECTED
    schedule_refresh.assert_called_once()

    coordinator.async_set_updated_data([model])
    assert coordinator.last_update_success
    assert coordinator.update_interval == UPDATE_INTERVAL_CONNECTED
    remove_listener()


async def test_close_delegates_to_api_helper(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mistral_client: MagicMock,
) -> None:
    """Coordinator shutdown releases the SDK client."""
    coordinator = MistralCoordinator(hass, mock_config_entry)
    coordinator._client = mock_mistral_client

    with patch(
        "custom_components.mistral_conversation.coordinator.async_close_client",
        new_callable=AsyncMock,
    ) as close_client:
        await coordinator.async_close()

    close_client.assert_awaited_once_with(mock_mistral_client)
