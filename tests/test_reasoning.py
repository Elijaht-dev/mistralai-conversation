"""Regression tests for reasoning controls through Home Assistant and the SDK."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from homeassistant.components import ai_task, conversation
from homeassistant.components.llm import LLMTools
from homeassistant.const import CONF_MODEL
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from mistralai.client import Mistral
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    CONF_REASONING_EFFORT,
    DOMAIN,
    SUBENTRY_TYPE_AI_TASK,
    SUBENTRY_TYPE_CONVERSATION,
    MistralModel,
)

from .helpers import completion_event


@pytest.fixture
def model() -> MistralModel:
    """Expose a model without reasoning, including its provider alias."""
    return MistralModel(
        id="ministral-14b-2512",
        aliases=("ministral-14b-latest",),
        function_calling=True,
        reasoning=False,
    )


@pytest.mark.parametrize(
    "subentry_type", [SUBENTRY_TYPE_CONVERSATION, SUBENTRY_TYPE_AI_TASK]
)
@pytest.mark.parametrize(
    ("model_id", "effort", "expected_effort"),
    [
        ("ministral-14b-2512", "none", None),
        ("ministral-14b-latest", "none", None),
        ("ministral-14b-latest", "invalid", None),
        ("custom-model", "none", None),
        ("custom-model", "high", "high"),
        ("reasoning-model", "none", "none"),
        ("reasoning-model", "high", "high"),
        ("ministral-14b-latest", "high", "model_no_reasoning"),
        ("ministral-14b-latest", "auto", None),
        ("custom-model", "auto", None),
        ("custom-model", "xhigh", "xhigh"),
        ("reasoning-model", "low", "low"),
        ("ft:mistral-small-2603:custom", "low", "low"),
        ("mistral-small-latest", "auto", None),
        ("mistral-small-latest", "none", "none"),
        ("mistral-small-latest", "high", "high"),
        ("mistral-small-2603", "none", "none"),
        ("mistral-small-2603", "high", "high"),
        ("mistral-medium-3-5", "high", "high"),
        ("magistral-small-latest", "auto", None),
        ("magistral-medium-latest", "auto", None),
        ("magistral-small-latest", "none", "model_reasoning_effort"),
        ("magistral-medium-latest", "high", "model_reasoning_effort"),
    ]
    + [
        (model_id, effort, "model_reasoning_effort")
        for model_id in ("mistral-small-latest", "mistral-medium-3-5")
        for effort in ("minimal", "low", "medium", "xhigh")
    ],
)
async def test_reasoning_request_body(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_provider: MagicMock,
    model: MistralModel,
    subentry_type: str,
    model_id: str,
    effort: str,
    expected_effort: str | None,
) -> None:
    """Serialize only supported reasoning controls and reject invalid requests."""
    rejected = expected_effort in {"model_no_reasoning", "model_reasoning_effort"}
    subentry = next(
        item
        for item in mock_config_entry.subentries.values()
        if item.subentry_type == subentry_type
    )
    hass.config_entries.async_update_subentry(
        mock_config_entry,
        subentry,
        data={
            **subentry.data,
            CONF_MODEL: model_id,
            CONF_REASONING_EFFORT: effort,
        },
    )
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    mock_config_entry.runtime_data.async_set_updated_data(
        [
            model,
            MistralModel(id="reasoning-model", reasoning=True),
            MistralModel(
                id="mistral-small-2603",
                aliases=("mistral-small-latest", "magistral-small-latest"),
                reasoning=True,
            ),
            MistralModel(id="mistral-medium-3-5", reasoning=True),
            MistralModel(
                id="magistral-small-latest",
                aliases=("magistral-small-2509",),
                reasoning=True,
            ),
            MistralModel(
                id="magistral-medium-2509",
                aliases=("magistral-medium-latest",),
                reasoning=True,
            ),
        ]
    )

    bodies: list[dict[str, object]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if expected_effort is None:
            assert "reasoning_effort" not in body
        else:
            assert body["reasoning_effort"] == expected_effort
        chunk = completion_event(content="Hello", finish_reason="stop").data
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {chunk.model_dump_json()}\n\ndata: [DONE]\n\n",
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client,
        Mistral(api_key="test-api-key", async_client=http_client) as client,
    ):
        mock_provider.chat.stream_async.side_effect = client.chat.stream_async
        if subentry_type == SUBENTRY_TYPE_AI_TASK:
            if rejected:
                with pytest.raises(HomeAssistantError) as raised:
                    await ai_task.async_generate_data(
                        hass,
                        task_name="Greeting",
                        entity_id="ai_task.mistral_ai_task",
                        instructions="Say hello",
                    )
                assert raised.value.translation_key == expected_effort
            else:
                task_result = await ai_task.async_generate_data(
                    hass,
                    task_name="Greeting",
                    entity_id="ai_task.mistral_ai_task",
                    instructions="Say hello",
                )
                assert task_result.data == "Hello"
        else:
            with patch(
                "homeassistant.components.llm.async_get_tools",
                new_callable=AsyncMock,
                return_value=LLMTools(tools=[]),
            ):
                result = await conversation.async_converse(
                    hass,
                    "Say hello",
                    None,
                    Context(),
                    agent_id="conversation.mistral_conversation",
                )
            if rejected:
                assert result.response.response_type is intent.IntentResponseType.ERROR
            else:
                assert (
                    result.response.response_type
                    is intent.IntentResponseType.ACTION_DONE
                )
                assert result.response.speech["plain"]["speech"] == "Hello"

    if rejected:
        mock_provider.chat.stream_async.assert_not_awaited()
        assert bodies == []
    else:
        mock_provider.chat.stream_async.assert_awaited_once()
        assert len(bodies) == 1
        assert bodies[0]["model"] == model_id
    assert subentry.data[CONF_REASONING_EFFORT] == effort
