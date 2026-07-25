"""Data update coordinator for Mistral AI Conversation."""

from __future__ import annotations

from datetime import timedelta
from typing import override

import httpx
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from mistralai.client import Mistral
from mistralai.client.errors import MistralError, NoResponseError

from .api import async_close_client, async_get_models, create_client
from .const import DOMAIN, LOGGER, ApiErrorKind, MistralModel
from .errors import api_error_message, classify_api_error

UPDATE_INTERVAL_CONNECTED = timedelta(hours=12)
UPDATE_INTERVAL_DISCONNECTED = timedelta(minutes=1)

type MistralConfigEntry = ConfigEntry[MistralCoordinator]


class MistralCoordinator(DataUpdateCoordinator[list[MistralModel]]):
    """Refresh model metadata and expose a shared Mistral client."""

    config_entry: MistralConfigEntry
    _client: Mistral

    def __init__(self, hass: HomeAssistant, config_entry: MistralConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=config_entry.title,
            update_interval=UPDATE_INTERVAL_CONNECTED,
            update_method=self.async_update_data,
            always_update=False,
        )

    @property
    def client(self) -> Mistral:
        """Return the shared Mistral client."""
        return self._client

    @override
    async def _async_setup(self) -> None:
        """Create the SDK client before the first refresh."""
        self._client = create_client(self.hass, self.config_entry.data[CONF_API_KEY])

    async def async_close(self) -> None:
        """Close resources created by the coordinator."""
        if hasattr(self, "_client"):
            await async_close_client(self._client)

    @callback
    @override
    def async_set_updated_data(self, data: list[MistralModel]) -> None:
        """Update data and restore the normal refresh interval."""
        self.update_interval = UPDATE_INTERVAL_CONNECTED
        super().async_set_updated_data(data)

    async def async_update_data(self) -> list[MistralModel]:
        """Fetch current model metadata from Mistral."""
        self.update_interval = UPDATE_INTERVAL_DISCONNECTED
        try:
            models = await async_get_models(self.client)
        except (
            MistralError,
            NoResponseError,
            httpx.HTTPError,
            TimeoutError,
        ) as err:
            error_kind = classify_api_error(err)
            message = api_error_message(err)

            if error_kind is ApiErrorKind.AUTHENTICATION:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="api_authentication_error",
                    translation_placeholders={"message": message},
                ) from err

            if error_kind is ApiErrorKind.TIMEOUT:
                raise TimeoutError(message) from err

            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"message": message},
            ) from err

        self.update_interval = UPDATE_INTERVAL_CONNECTED
        return models

    @callback
    def mark_connection_error(self) -> None:
        """Mark entities unavailable and schedule a short-interval refresh."""
        self.update_interval = UPDATE_INTERVAL_DISCONNECTED
        if self.last_update_success:
            self.last_update_success = False
            self.async_update_listeners()
            if self._listeners and not self.hass.is_stopping:
                self._schedule_refresh()

    @callback
    def get_model_info(self, model_id: str) -> tuple[MistralModel, bool]:
        """Return cached metadata and whether the model was recognized."""
        for model in self.data or []:
            if model.matches(model_id):
                return model, True
        return MistralModel(id=model_id), False
