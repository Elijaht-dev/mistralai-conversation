"""Tests for the Mistral AI Task platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from homeassistant.components import ai_task
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from mistralai.client.models import ResponseFormat
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import SUBENTRY_TYPE_AI_TASK

from .helpers import completion_event, event_stream

ENTITY_ID = "ai_task.mistral_ai_task"


async def test_generate_unstructured_data(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """An AI Task response is generated through the shared chat pipeline."""
    mock_init_component.chat.stream_async.return_value = event_stream(
        [completion_event(content="The garage is closed.", finish_reason="stop")]
    )

    result = await ai_task.async_generate_data(
        hass,
        task_name="Summarize garage",
        entity_id=ENTITY_ID,
        instructions="Summarize the garage state",
    )

    assert result.data == "The garage is closed."
    call = mock_init_component.chat.stream_async.await_args
    assert call.kwargs["response_format"] is None
    entity_entry = entity_registry.async_get(ENTITY_ID)
    assert entity_entry is not None
    ai_task_subentry = next(
        subentry
        for subentry in mock_config_entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_AI_TASK
    )
    assert entity_entry.config_subentry_id == ai_task_subentry.subentry_id
    assert (
        entity_entry.supported_features
        == ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )


async def test_generate_structured_data_uses_native_json_schema(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """Home Assistant structures become strict Mistral JSON-schema output."""
    mock_init_component.chat.stream_async.return_value = event_stream(
        [
            completion_event(
                content='{"characters":["Mario","Luigi"]}',
                finish_reason="stop",
            )
        ]
    )
    structure = vol.Schema({vol.Required("characters"): [str]})

    result = await ai_task.async_generate_data(
        hass,
        task_name="Game Characters",
        entity_id=ENTITY_ID,
        instructions="Return two characters",
        structure=structure,
    )

    assert result.data == {"characters": ["Mario", "Luigi"]}
    response_format = mock_init_component.chat.stream_async.await_args.kwargs[
        "response_format"
    ]
    assert isinstance(response_format, ResponseFormat)
    assert response_format.type == "json_schema"
    assert response_format.json_schema is not None
    assert response_format.json_schema.name == "game_characters"
    assert response_format.json_schema.strict is True
    schema = response_format.json_schema.schema_definition
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["characters"]["type"] == "array"


async def test_generate_structured_data_rejects_invalid_json(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """Invalid structured output is surfaced as a translated task error."""
    mock_init_component.chat.stream_async.return_value = event_stream(
        [completion_event(content="not-json", finish_reason="stop")]
    )

    with pytest.raises(HomeAssistantError) as raised:
        await ai_task.async_generate_data(
            hass,
            task_name="Structured task",
            entity_id=ENTITY_ID,
            instructions="Return structured data",
            structure=vol.Schema({vol.Required("answer"): str}),
        )

    assert raised.value.translation_key == "json_parse_error"
