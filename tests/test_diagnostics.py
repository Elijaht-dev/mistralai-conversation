"""Tests for redacted config-entry diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import CONF_API_KEY, CONF_PROMPT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_entry_diagnostics_are_complete_and_redacted(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Diagnostics include capability state without credentials or prompts."""
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    serialized = repr(diagnostics)
    assert "test-api-key" not in serialized
    assert str(mock_config_entry.data[CONF_API_KEY]) not in serialized
    assert mock_config_entry.entry_id in serialized
    assert diagnostics["client"].startswith("mistralai==")
    assert diagnostics["entry_version"] == "1.2"
    assert diagnostics["coordinator"]["last_update_success"]
    assert diagnostics["coordinator"]["models"][0]["function_calling"]
    assert diagnostics["entities"]

    subentry_data = next(iter(diagnostics["subentries"].values()))["data"]
    assert (
        subentry_data[CONF_PROMPT]
        != next(iter(mock_config_entry.subentries.values())).data[CONF_PROMPT]
    )
