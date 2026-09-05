"""Tests for model-specific reasoning choices and reconfiguration."""

from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_MODEL, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    CONF_REASONING_EFFORT,
    DEFAULT_CONVERSATION_OPTIONS,
    DOMAIN,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
    MistralModel,
)


@pytest.mark.parametrize(
    "subentry_type", [SUBENTRY_TYPE_CONVERSATION, SUBENTRY_TYPE_AI_TASK]
)
@pytest.mark.parametrize(
    ("model", "effort", "choices", "error_key"),
    [
        (
            MistralModel(
                id="mistral-small-latest", reasoning=True, function_calling=True
            ),
            "low",
            ["auto", "none", "high"],
            "model_reasoning_effort",
        ),
        (
            MistralModel(
                id="mistral-medium-3-5", reasoning=True, function_calling=True
            ),
            "medium",
            ["auto", "none", "high"],
            "model_reasoning_effort",
        ),
        (
            MistralModel(
                id="magistral-small-2509", reasoning=True, function_calling=True
            ),
            "none",
            {"value": "auto", "translation_key": "reasoning_always_on.options"},
            None,
        ),
        (
            MistralModel(id="ministral-14b-latest", function_calling=True),
            "high",
            {"value": "none", "translation_key": "reasoning_unavailable.options"},
            None,
        ),
        (
            MistralModel(id="ft:custom-model", reasoning=True, function_calling=True),
            "xhigh",
            ["auto", "none", "high"],
            None,
        ),
    ],
)
async def test_reconfigure_reasoning_choices(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_provider: MagicMock,
    model: MistralModel,
    subentry_type: str,
    effort: str,
    choices: list[str] | dict[str, str],
    error_key: str | None,
) -> None:
    """Saved incompatible efforts remain visible until explicitly corrected."""
    subentry = next(
        item
        for item in mock_config_entry.subentries.values()
        if item.subentry_type == subentry_type
    )
    data = {**subentry.data, CONF_MODEL: model.id, CONF_REASONING_EFFORT: effort}
    hass.config_entries.async_update_subentry(mock_config_entry, subentry, data=data)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    result = await mock_config_entry.start_subentry_reconfigure_flow(
        hass, subentry.subentry_id
    )
    assert result["type"] is FlowResultType.FORM
    selector = next(
        value
        for key, value in result["data_schema"].schema.items()
        if key.schema == CONF_REASONING_EFFORT
    )
    if isinstance(choices, dict):
        assert selector.selector_type == "constant"
        assert selector.config == choices
        # Match the frontend's constant-selector lookup in both UI languages.
        translation_path = (
            f"component.{DOMAIN}.selector.{choices['translation_key']}.value"
        )
        for language in ("en", "fr"):
            translations = await async_get_translations(
                hass, language, "selector", {DOMAIN}
            )
            assert (
                translations[translation_path]
                == {
                    "en": {
                        "auto": "Reasoning always active",
                        "none": "Reasoning unavailable",
                    },
                    "fr": {
                        "auto": "Raisonnement toujours actif",
                        "none": "Raisonnement non disponible",
                    },
                }[language][choices["value"]]
            )
    else:
        assert selector.config["options"] == choices
    assert subentry.data == data

    user_input = {**data, CONF_NAME: subentry.title}
    if isinstance(choices, dict):
        user_input[CONF_REASONING_EFFORT] = choices["value"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], user_input
    )
    if error_key:
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_REASONING_EFFORT: error_key}
        assert subentry.data == data
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {**user_input, CONF_REASONING_EFFORT: "auto"}
        )
        assert subentry.data[CONF_REASONING_EFFORT] == "auto"
    else:
        assert subentry.data[CONF_REASONING_EFFORT] == user_input[CONF_REASONING_EFFORT]
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    mock_provider.chat.stream_async.assert_not_awaited()


async def test_model_change_revalidates_reasoning(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """A single form submission can select a custom model with a new effort."""
    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONVERSATION),
        context={"source": config_entries.SOURCE_USER},
    )
    # Small's initial list omits xhigh, but a custom model can still accept it.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **DEFAULT_CONVERSATION_OPTIONS,
            CONF_NAME: "Custom assistant",
            CONF_MODEL: "ft:custom-model",
            CONF_REASONING_EFFORT: "xhigh",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REASONING_EFFORT] == "xhigh"


async def test_custom_reasoning_rejects_invalid_value(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Manually entered efforts must still be recognized settings."""
    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_CONVERSATION),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            **DEFAULT_CONVERSATION_OPTIONS,
            CONF_NAME: "Custom assistant",
            CONF_MODEL: "ft:custom-model",
            CONF_REASONING_EFFORT: "invalid",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_REASONING_EFFORT: "model_reasoning_effort"}


@pytest.mark.parametrize(
    "subentry_type", [SUBENTRY_TYPE_CONVERSATION, SUBENTRY_TYPE_AI_TASK]
)
@pytest.mark.parametrize(
    ("initial_model", "initial_effort", "new_model", "new_effort"),
    [
        ("ministral-14b-latest", "none", "mistral-small-latest", "high"),
        ("mistral-small-latest", "none", "magistral-small-latest", "auto"),
        ("magistral-small-latest", "auto", "ministral-14b-latest", "auto"),
        ("mistral-small-latest", "high", "ministral-14b-latest", "none"),
        ("magistral-small-latest", "auto", "mistral-small-latest", "high"),
    ],
)
async def test_changed_reasoning_control_requires_review(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_provider: MagicMock,
    subentry_type: str,
    initial_model: str,
    initial_effort: str,
    new_model: str,
    new_effort: str,
) -> None:
    """Changing the control presents the new state before writing any settings."""
    subentry = next(
        item
        for item in mock_config_entry.subentries.values()
        if item.subentry_type == subentry_type
    )
    initial_data = {
        **subentry.data,
        CONF_MODEL: initial_model,
        CONF_REASONING_EFFORT: initial_effort,
    }
    hass.config_entries.async_update_subentry(
        mock_config_entry, subentry, data=initial_data
    )
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    mock_config_entry.runtime_data.async_set_updated_data(
        [
            MistralModel(id="ministral-14b-latest", function_calling=True),
            MistralModel(
                id="mistral-small-latest", reasoning=True, function_calling=True
            ),
            MistralModel(
                id="magistral-small-latest", reasoning=True, function_calling=True
            ),
        ]
    )
    result = await mock_config_entry.start_subentry_reconfigure_flow(
        hass, subentry.subentry_id
    )
    changed_data = {**initial_data, CONF_NAME: subentry.title, CONF_MODEL: new_model}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], changed_data
    )
    assert result["type"] is FlowResultType.FORM
    assert subentry.data == initial_data
    selector = next(
        value
        for key, value in result["data_schema"].schema.items()
        if key.schema == CONF_REASONING_EFFORT
    )
    if new_model == "mistral-small-latest":
        assert selector.config["options"] == ["auto", "none", "high"]
    else:
        assert selector.config["value"] == new_effort
    # Validate the actual rendered schema as well as the public flow handler.
    confirmed_data = result["data_schema"](
        {**changed_data, CONF_REASONING_EFFORT: new_effort}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], confirmed_data
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert subentry.data[CONF_MODEL] == new_model
    assert subentry.data[CONF_REASONING_EFFORT] == new_effort
