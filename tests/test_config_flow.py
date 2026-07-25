"""Tests for Mistral config and conversation-subentry flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LLM_HASS_API,
    CONF_MODEL,
    CONF_NAME,
    CONF_PROMPT,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from mistralai.client.errors import NoResponseError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SAFE_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_CONVERSATION_OPTIONS,
    DOMAIN,
    MistralModel,
)

from .helpers import mistral_error


async def test_user_form_and_create_entry(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
) -> None:
    """A valid key creates an account entry and default conversation agent."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] is None

    with patch(
        "custom_components.mistral_conversation.config_flow.async_validate_api_key",
        new_callable=AsyncMock,
        return_value=[],
    ) as validate:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "new-key"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mistral AI"
    assert result["data"] == {CONF_API_KEY: "new-key"}
    assert result["subentries"] == [
        {
            "subentry_type": "conversation",
            "data": DEFAULT_CONVERSATION_OPTIONS,
            "title": DEFAULT_CONVERSATION_NAME,
            "unique_id": None,
        }
    ]
    validate.assert_awaited_once_with(hass, "new-key")
    mock_setup_entry.assert_awaited_once()


async def test_duplicate_entry_aborts(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The same API key cannot be configured twice."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_API_KEY: mock_config_entry.data[CONF_API_KEY]},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (mistral_error(401), "invalid_auth"),
        (mistral_error(408), "timeout_connect"),
        (mistral_error(500), "cannot_connect"),
        (NoResponseError("no response"), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_user_form_errors(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    error: BaseException,
    error_key: str,
) -> None:
    """Provider and unexpected errors are presented with stable flow keys."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    with patch(
        "custom_components.mistral_conversation.config_flow.async_validate_api_key",
        new_callable=AsyncMock,
        side_effect=error,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "bad-key"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error_key}
    mock_setup_entry.assert_not_awaited()


async def test_reauthentication_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """A valid replacement key updates and reloads the existing entry."""
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.mistral_conversation.config_flow.async_validate_api_key",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "replacement-key"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_KEY] == "replacement-key"


async def test_reauthentication_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """An invalid replacement key leaves the saved credential unchanged."""
    result = await mock_config_entry.start_reauth_flow(hass)
    with patch(
        "custom_components.mistral_conversation.config_flow.async_validate_api_key",
        new_callable=AsyncMock,
        side_effect=mistral_error(401, "bad key"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "bad-key"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_API_KEY] == "test-api-key"


def _conversation_input(**updates: object) -> dict[str, object]:
    """Return complete valid input for a conversation subentry."""
    return {
        CONF_NAME: "Kitchen assistant",
        **DEFAULT_CONVERSATION_OPTIONS,
        **updates,
    }


async def test_create_conversation_subentry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """A loaded account can create additional conversation agents."""
    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, "conversation"),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        _conversation_input(),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen assistant"
    assert CONF_NAME not in result["data"]
    assert result["data"][CONF_MODEL] == "mistral-small-latest"


async def test_create_chat_only_subentry_removes_empty_api_list(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """An empty tool selector is persisted as chat-only configuration."""
    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, "conversation"),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        _conversation_input(**{CONF_LLM_HASS_API: []}),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_LLM_HASS_API not in result["data"]


@pytest.mark.parametrize(
    ("model", "updates", "field", "error_key"),
    [
        (
            MistralModel(id="limited"),
            {CONF_MODEL: "limited"},
            CONF_MODEL,
            "model_no_tools",
        ),
        (
            MistralModel(id="limited", function_calling=True),
            {
                CONF_MODEL: "limited",
                CONF_REASONING_EFFORT: "high",
            },
            CONF_REASONING_EFFORT,
            "model_no_reasoning",
        ),
        (
            MistralModel(
                id="limited",
                function_calling=True,
                reasoning=True,
                max_context_length=1024,
            ),
            {
                CONF_MODEL: "limited",
                CONF_MAX_TOKENS: 2048,
            },
            CONF_MAX_TOKENS,
            "max_tokens_context",
        ),
    ],
)
async def test_subentry_capability_validation(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    model: MistralModel,
    updates: dict[str, object],
    field: str,
    error_key: str,
) -> None:
    """Known model metadata prevents incompatible agent settings."""
    mock_config_entry.runtime_data.async_set_updated_data([model])
    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, "conversation"),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        _conversation_input(**updates),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error_key}


async def test_custom_model_is_permitted(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Unknown custom and fine-tuned IDs remain available without false gating."""
    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, "conversation"),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        _conversation_input(
            **{
                CONF_MODEL: "ft:custom-model",
                CONF_REASONING_EFFORT: "xhigh",
            }
        ),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MODEL] == "ft:custom-model"


async def test_reconfigure_conversation_subentry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Existing subentry options and title can be updated."""
    subentry = next(iter(mock_config_entry.subentries.values()))
    result = await mock_config_entry.start_subentry_reconfigure_flow(
        hass, subentry.subentry_id
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        _conversation_input(
            **{
                CONF_NAME: "Updated assistant",
                CONF_PROMPT: "Be concise",
                CONF_TEMPERATURE: 0.4,
                CONF_SAFE_PROMPT: True,
            }
        ),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert subentry.title == "Updated assistant"
    assert subentry.data[CONF_PROMPT] == "Be concise"
    assert subentry.data[CONF_TEMPERATURE] == 0.4
    assert subentry.data[CONF_SAFE_PROMPT] is True


async def test_subentry_flow_aborts_when_entry_not_loaded(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Subentry editing is unavailable while its account is unloaded."""
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, "conversation"),
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"
