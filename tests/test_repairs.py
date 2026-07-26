"""Tests for Mistral AI Conversation repair flows."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.repairs import async_create_fix_flow


async def test_deprecated_model_repair(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """A repair can select and persist a replacement model."""
    subentry = next(iter(mock_config_entry.subentries.values()))
    flow = await async_create_fix_flow(
        hass,
        f"model_deprecated_{subentry.subentry_id}",
        {
            "entry_id": mock_config_entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "replacement": "mistral-small-latest",
        },
    )
    flow.hass = hass

    form = await flow.async_step_init()

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "init"
    assert form["description_placeholders"] == {
        "model": "mistral-small-latest",
        "subentry_name": "Mistral conversation",
    }

    result = await flow.async_step_init({CONF_MODEL: "replacement-model"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (
        mock_config_entry.subentries[subentry.subentry_id].data[CONF_MODEL]
        == "replacement-model"
    )


@pytest.mark.parametrize(
    ("issue_id", "data", "translation_key"),
    [
        ("other_issue", {}, "unknown_issue_id"),
        ("model_deprecated_id", None, "unknown_issue_id"),
        (
            "model_deprecated_id",
            {"entry_id": "", "subentry_id": "id", "replacement": "model"},
            "invalid_repair_data",
        ),
    ],
)
async def test_invalid_repair_metadata(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str] | None,
    translation_key: str,
) -> None:
    """Invalid issue identifiers and metadata are rejected safely."""
    with pytest.raises(HomeAssistantError) as error:
        await async_create_fix_flow(hass, issue_id, data)

    assert error.value.translation_key == translation_key


async def test_repair_target_was_removed(
    hass: HomeAssistant,
) -> None:
    """A stale repair reports that its configured entity no longer exists."""
    flow = await async_create_fix_flow(
        hass,
        "model_deprecated_removed",
        {
            "entry_id": "removed",
            "subentry_id": "removed",
            "replacement": "replacement-model",
        },
    )
    flow.hass = hass

    with pytest.raises(HomeAssistantError) as error:
        await flow.async_step_init()

    assert error.value.translation_key == "subentry_not_found"
