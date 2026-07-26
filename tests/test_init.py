"""Tests for integration setup, unload, migration, and repair issues."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LLM_HASS_API,
    CONF_MODEL,
    CONF_PROMPT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation import (
    async_migrate_entry,
    async_remove_entry,
)
from custom_components.mistral_conversation.const import (
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SAFE_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_CONVERSATION_OPTIONS,
    DEFAULT_STT_OPTIONS,
    DOMAIN,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
    SUBENTRY_TYPE_STT,
    MistralModel,
)

from .helpers import mistral_error


async def test_setup_creates_entity_and_service_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A loaded subentry creates one registered conversation service."""
    state = hass.states.get("conversation.mistral_conversation")
    assert state is not None
    subentry = next(iter(mock_config_entry.subentries.values()))

    entity = entity_registry.async_get("conversation.mistral_conversation")
    assert entity is not None
    assert entity.unique_id == subentry.subentry_id
    assert entity.config_entry_id == mock_config_entry.entry_id
    assert entity.config_subentry_id == subentry.subentry_id
    assert entity.translation_key == "conversation"

    device = device_registry.async_get_device({(DOMAIN, subentry.subentry_id)})
    assert device is not None
    assert device.name == "Mistral conversation"
    assert device.manufacturer == "Mistral AI"
    assert device.model == "Mistral Small"
    assert device.model_id == "mistral-small-latest"
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_unload_closes_client(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Unloading removes entities and closes SDK resources."""
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert (
        conversation.async_get_agent(hass, "conversation.mistral_conversation") is None
    )
    mock_init_component.__exit__.assert_called_once_with(None, None, None)
    mock_init_component.__aexit__.assert_awaited_once_with(None, None, None)


async def test_setup_auth_failure_closes_client(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mistral_client: MagicMock,
) -> None:
    """A first-refresh auth failure starts reauth and releases resources."""
    with (
        patch(
            "custom_components.mistral_conversation.coordinator.create_client",
            return_value=mock_mistral_client,
        ),
        patch(
            "custom_components.mistral_conversation.coordinator.async_get_models",
            new_callable=AsyncMock,
            side_effect=mistral_error(401, "bad key"),
        ),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_mistral_client.__exit__.assert_called_once()
    mock_mistral_client.__aexit__.assert_awaited_once()


async def test_setup_connection_failure_enters_retry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_mistral_client: MagicMock,
) -> None:
    """Transient first-refresh failures leave the entry retryable."""
    with (
        patch(
            "custom_components.mistral_conversation.coordinator.create_client",
            return_value=mock_mistral_client,
        ),
        patch(
            "custom_components.mistral_conversation.coordinator.async_get_models",
            new_callable=AsyncMock,
            side_effect=mistral_error(503, "offline"),
        ),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_mistral_client.__aexit__.assert_awaited_once()


async def test_deprecation_issue_created_and_cleared(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Coordinator refreshes keep model-retirement issues current."""
    subentry = next(iter(mock_config_entry.subentries.values()))
    issue_id = f"model_deprecated_{subentry.subentry_id}"
    deprecated = MistralModel(
        id="mistral-small-latest",
        deprecation=datetime(2027, 1, 2, tzinfo=UTC),
        replacement_model="mistral-small-next",
    )

    mock_config_entry.runtime_data.async_set_updated_data([deprecated])
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_config_entry.runtime_data.async_set_updated_data(
        [MistralModel(id="mistral-small-latest")]
    )
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_remove_entry_clears_deprecation_issue(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Deleting an entry removes its nonpersistent repair records."""
    subentry = next(iter(mock_config_entry.subentries.values()))
    issue_id = f"model_deprecated_{subentry.subentry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="model_deprecated",
    )

    await async_remove_entry(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_migration_normalizes_preview_data(
    hass: HomeAssistant,
) -> None:
    """Version 1.1 values are normalized without enabling removed APIs."""
    entry = MockConfigEntry(
        title="Mistral AI",
        domain=DOMAIN,
        data={CONF_API_KEY: "test-api-key"},
        version=1,
        minor_version=1,
        subentries_data=[
            {
                "data": {
                    CONF_MODEL: "custom",
                    CONF_PROMPT: "Custom prompt",
                    CONF_MAX_TOKENS: -1,
                    CONF_TEMPERATURE: 4,
                    CONF_REASONING_EFFORT: "unsupported",
                    CONF_SAFE_PROMPT: "yes",
                },
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Chat only",
                "unique_id": None,
            },
            {
                "data": {
                    **DEFAULT_CONVERSATION_OPTIONS,
                    CONF_LLM_HASS_API: "assist",
                },
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Legacy API",
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 3
    chat_only, legacy = [
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_CONVERSATION
    ]
    assert CONF_LLM_HASS_API not in chat_only.data
    assert chat_only.data[CONF_MODEL] == "custom"
    assert chat_only.data[CONF_PROMPT] == "Custom prompt"
    assert chat_only.data[CONF_MAX_TOKENS] == 2048
    assert chat_only.data[CONF_TEMPERATURE] == 0.2
    assert chat_only.data[CONF_REASONING_EFFORT] == "none"
    assert chat_only.data[CONF_SAFE_PROMPT] is False
    assert legacy.data[CONF_LLM_HASS_API] == ["assist"]
    ai_task_entry = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_AI_TASK
    )
    stt_entry = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_STT
    )
    assert ai_task_entry.data == DEFAULT_AI_TASK_OPTIONS
    assert stt_entry.data == DEFAULT_STT_OPTIONS


async def test_migration_does_not_duplicate_voice_platform_subentries(
    hass: HomeAssistant,
) -> None:
    """Version 1.2 accounts keep existing AI Task and STT subentries."""
    entry = MockConfigEntry(
        title="Mistral AI",
        domain=DOMAIN,
        data={CONF_API_KEY: "test-api-key"},
        version=1,
        minor_version=2,
        subentries_data=[
            {
                "data": DEFAULT_CONVERSATION_OPTIONS,
                "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                "title": "Conversation",
                "unique_id": None,
            },
            {
                "data": DEFAULT_AI_TASK_OPTIONS,
                "subentry_type": SUBENTRY_TYPE_AI_TASK,
                "title": "AI Task",
                "unique_id": None,
            },
            {
                "data": DEFAULT_STT_OPTIONS,
                "subentry_type": SUBENTRY_TYPE_STT,
                "title": "STT",
                "unique_id": None,
            },
        ],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 3
    assert [subentry.subentry_type for subentry in entry.subentries.values()].count(
        SUBENTRY_TYPE_AI_TASK
    ) == 1
    assert [subentry.subentry_type for subentry in entry.subentries.values()].count(
        SUBENTRY_TYPE_STT
    ) == 1


async def test_migration_rejects_unknown_major_version(
    hass: HomeAssistant,
) -> None:
    """Future major entry formats are not modified speculatively."""
    entry = MockConfigEntry(
        title="Future",
        domain=DOMAIN,
        data={CONF_API_KEY: "test-api-key"},
        version=2,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert not await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.minor_version == 1
