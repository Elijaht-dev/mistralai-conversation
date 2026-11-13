"""Prompt cache routing and provider-reported token usage through Home Assistant."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from unittest.mock import MagicMock, patch

import httpx2
import pytest
import voluptuous as vol
from homeassistant.components import conversation
from homeassistant.components.conversation import trace
from homeassistant.const import CONF_API_KEY, CONF_MODEL, CONF_PROMPT
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from mistralai.client import Mistral

from custom_components.mistral_conversation.const import (
    DEFAULT_CONVERSATION_OPTIONS,
    DOMAIN,
    SUBENTRY_TYPE_CONVERSATION,
)
from tests.ha import MockConfigEntry, mock_llm_tools

from .helpers import completion_event, event_stream

ENTITY_ID = "conversation.mistral_conversation"


@pytest.fixture
def chat_requests(mock_mistral_client: MagicMock) -> list[dict]:
    """Capture each request before the tool loop appends response messages."""
    requests = []

    async def respond(**kwargs):
        requests.append(deepcopy(kwargs))
        return event_stream([completion_event(content="Hello", finish_reason="stop")])

    mock_mistral_client.chat.stream_async.side_effect = respond
    return requests


async def test_cache_key_reused_across_conversations_and_reload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    chat_requests: list[dict],
) -> None:
    """A stable routing key shares prefixes without sharing conversation history."""
    with mock_llm_tools([]):
        first = await conversation.async_converse(
            hass, "First conversation", None, Context(), agent_id=ENTITY_ID
        )
        second = await conversation.async_converse(
            hass, "Second conversation", None, Context(), agent_id=ENTITY_ID
        )
        follow_up = await conversation.async_converse(
            hass, "Follow up", first.conversation_id, Context(), agent_id=ENTITY_ID
        )
        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        after_reload = await conversation.async_converse(
            hass, "After reload", None, Context(), agent_id=ENTITY_ID
        )

    assert first.conversation_id != second.conversation_id
    assert follow_up.conversation_id == first.conversation_id
    assert after_reload.response.speech["plain"]["speech"] == "Hello"
    assert len(chat_requests) == 4
    keys = {request["prompt_cache_key"] for request in chat_requests}
    assert len(keys) == 1
    key = keys.pop()
    assert len(key) == 64
    assert set(key) <= set("0123456789abcdef")
    assert mock_config_entry.entry_id not in key
    assert all(
        subentry.subentry_id not in key
        for subentry in mock_config_entry.subentries.values()
    )
    assert [message.content for message in chat_requests[1]["messages"][1:]] == [
        "Second conversation"
    ]
    assert [message.content for message in chat_requests[2]["messages"][1:]] == [
        "First conversation",
        "Hello",
        "Follow up",
    ]
    assert [message.content for message in chat_requests[3]["messages"][1:]] == [
        "After reload"
    ]


async def test_cache_key_changes_when_entity_model_changes(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    chat_requests: list[dict],
) -> None:
    """Reconfiguring one entity's model selects a distinct cache namespace."""
    with mock_llm_tools([]):
        await conversation.async_converse(
            hass, "Hello", None, Context(), agent_id=ENTITY_ID
        )
        subentry = next(
            subentry
            for subentry in mock_config_entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_CONVERSATION
        )
        assert hass.config_entries.async_update_subentry(
            mock_config_entry,
            subentry,
            data=dict(subentry.data) | {CONF_MODEL: "custom-model"},
        )
        await hass.async_block_till_done()
        result = await conversation.async_converse(
            hass, "Hello", None, Context(), agent_id=ENTITY_ID
        )

    assert result.response.speech["plain"]["speech"] == "Hello"
    first, second = chat_requests
    assert first["model"] == "mistral-small-latest"
    assert second["model"] == "custom-model"
    assert first["prompt_cache_key"] != second["prompt_cache_key"]


async def test_cache_keys_distinct_for_accounts_entities_and_models(
    hass: HomeAssistant,
    mock_provider: MagicMock,
    chat_requests: list[dict],
    entity_registry: er.EntityRegistry,
) -> None:
    """Each account/entity/model combination gets its own opaque routing key."""
    for models in (
        ["mistral-small-latest", "mistral-small-latest", "custom-model"],
        ["mistral-small-latest"],
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test-api-key"},
            subentries_data=[
                {
                    "data": DEFAULT_CONVERSATION_OPTIONS | {CONF_MODEL: model},
                    "subentry_type": SUBENTRY_TYPE_CONVERSATION,
                    "title": f"Voice {index}",
                    "unique_id": None,
                }
                for index, model in enumerate(models)
            ],
        )
        entry.add_to_hass(hass)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    agents = [
        entity.entity_id
        for entity in entity_registry.entities.values()
        if entity.platform == DOMAIN and entity.domain == conversation.DOMAIN
    ]
    assert len(agents) == 4
    with mock_llm_tools([]):
        for agent_id in agents:
            result = await conversation.async_converse(
                hass, "Hello", None, Context(), agent_id=agent_id
            )
            assert result.response.speech["plain"]["speech"] == "Hello"

    assert len({request["prompt_cache_key"] for request in chat_requests}) == 4


async def test_new_conversations_send_changed_prompt_and_tools(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    chat_requests: list[dict],
) -> None:
    """Cache routing never replaces HA's current instructions or tool exposure."""
    tools = []
    for name in ("GetKitchen", "GetBedroom"):
        tool = MagicMock()
        tool.name = name
        tool.description = f"Read {name}"
        tool.parameters = vol.Schema({vol.Required("name"): str})
        tools.append(tool)
    with mock_llm_tools([tools[0]]):
        await conversation.async_converse(
            hass, "First request", None, Context(), agent_id=ENTITY_ID
        )

    subentry = next(
        subentry
        for subentry in mock_config_entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_CONVERSATION
    )
    assert hass.config_entries.async_update_subentry(
        mock_config_entry,
        subentry,
        data=dict(subentry.data) | {CONF_PROMPT: "Use the revised instructions."},
    )
    await hass.async_block_till_done()
    with mock_llm_tools([tools[1]]):
        await conversation.async_converse(
            hass, "Second request", None, Context(), agent_id=ENTITY_ID
        )

    first, second = chat_requests
    assert first["prompt_cache_key"] == second["prompt_cache_key"]
    assert "Use the revised instructions." not in first["messages"][0].content
    assert "Use the revised instructions." in second["messages"][0].content
    assert [tool.function.name for tool in first["tools"]] == ["GetKitchen"]
    assert [tool.function.name for tool in second["tools"]] == ["GetBedroom"]
    assert second["messages"][1].content == "Second request"


@pytest.mark.parametrize(
    ("usage_fields", "cached_tokens"),
    [
        ({"prompt_tokens_details": {"cached_tokens": 64}}, 64),
        ({"prompt_tokens_details": {"cached_tokens": 128}}, 128),
        ({"prompt_tokens_details": {"cached_tokens": 0}}, 0),
        ({}, None),
        ({"prompt_tokens_details": None}, None),
        ({"prompt_tokens_details": {}}, None),
        ({"prompt_tokens_details": "invalid"}, None),
        ({"prompt_tokens_details": []}, None),
        ({"prompt_tokens_details": {"cached_tokens": -1}}, None),
        ({"prompt_tokens_details": {"cached_tokens": 129}}, None),
        ({"prompt_tokens_details": {"cached_tokens": True}}, None),
        ({"prompt_tokens_details": {"cached_tokens": False}}, None),
        ({"prompt_tokens_details": {"cached_tokens": "64"}}, None),
        ({"prompt_tokens_details": {"cached_tokens": 1.5}}, None),
        ({"prompt_tokens_details": {"cached_tokens": None}}, None),
        ({"prompt_tokens_details": {"cached_tokens": []}}, None),
        ({"prompt_tokens": None, "prompt_tokens_details": {"cached_tokens": 64}}, None),
    ],
)
async def test_cached_token_traces_through_real_sdk(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    caplog: pytest.LogCaptureFixture,
    usage_fields: dict,
    cached_tokens: int | None,
) -> None:
    """The SDK parses usage-only events; only valid numeric cache usage is traced."""
    caplog.set_level(logging.DEBUG, logger="custom_components.mistral_conversation")
    requests = []
    usage = {
        "prompt_tokens": 128,
        "completion_tokens": 3,
        "total_tokens": 131,
    } | usage_fields

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        chunk = {
            "id": "test-completion",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "mistral-small-latest",
        }
        text_event = chunk | {
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ]
        }
        usage_event = chunk | {"choices": [], "usage": usage}
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                f"data: {json.dumps(text_event)}\n\n"
                f"data: {json.dumps(usage_event)}\n\n"
                "data: [DONE]\n\n"
            ),
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as transport:
        sdk = await hass.async_add_executor_job(
            lambda: Mistral(api_key="test-api-key", async_client=transport)
        )
        async with sdk:
            chat = await hass.async_add_executor_job(lambda: sdk.chat)
            with patch.object(mock_init_component, "chat", chat), mock_llm_tools([]):
                result = await conversation.async_converse(
                    hass, "Hello", None, Context(), agent_id=ENTITY_ID
                )

    assert result.response.speech["plain"]["speech"] == "Hello"
    assert len(requests) == 1
    assert len(requests[0]["prompt_cache_key"]) == 64
    assert requests[0]["messages"][0]["role"] == "system"
    assert requests[0]["messages"][1]["content"] == "Hello"
    stats = [
        event["data"]["stats"]
        for event in trace.async_get_traces()[-1].as_dict()["events"]
        if event["data"] and "stats" in event["data"]
    ]
    expected = {"input_tokens": usage["prompt_tokens"] or 0, "output_tokens": 3}
    if cached_tokens is not None:
        expected["cached_input_tokens"] = cached_tokens
    assert stats == [expected]
    assert [
        record.getMessage()
        for record in caplog.records
        if record.msg == "Mistral token usage: %s"
    ] == [f"Mistral token usage: {expected}"]
