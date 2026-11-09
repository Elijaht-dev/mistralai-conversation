"""Public Home Assistant AI Task evaluation behavior."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from homeassistant.components import ai_task
from homeassistant.const import CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util.json import JsonValueType
from mistralai.client import Mistral
from mistralai.client.models import (
    CompletionEvent,
    DocumentURLChunk,
    FunctionCall,
    ImageURLChunk,
    TextChunk,
    ThinkChunk,
    ToolCall,
)
from mistralai.client.utils.eventstreaming import EventStreamAsync
from PIL import Image

from custom_components.mistral_conversation.const import (
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SAFE_PROMPT,
    CONF_TEMPERATURE,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    REQUEST_TIMEOUT_MS,
    SUBENTRY_TYPE_AI_TASK,
    MistralModel,
)
from tests.ha import MockConfigEntry

from .helpers import completion_event, event_stream, mistral_error

pytestmark = pytest.mark.skipif(
    not hasattr(ai_task, "async_evaluate"), reason="Evaluation requires HA 2026.11"
)

ENTITY_ID = "ai_task.mistral_ai_task"
QUESTIONS: dict[str, ai_task.EvaluationQuestion] = {
    "warm": {"type": "noul", "instructions": "Is the room warm?"},
    # Use a valid criterion and question ID that is also a JSON Schema keyword.
    "properties": {
        "type": "choice",
        "instructions": "What is the room like?",
        "criteria": {"properties": "Comfortable", "too_warm": "Too warm"},
    },
    "warmth": {
        "type": "score",
        "instructions": "How warm is the room?",
        "criteria": ["Cool", "Comfortable", "Warm"],
    },
}


@pytest.fixture
def configured_model(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> str:
    """Give the existing entity a custom data model and nondefault settings."""
    subentry = next(
        entry
        for entry in mock_config_entry.subentries.values()
        if entry.subentry_type == SUBENTRY_TYPE_AI_TASK
    )
    model = "ft:test-evaluation-model"
    hass.config_entries.async_update_subentry(
        mock_config_entry,
        subentry,
        data={
            **subentry.data,
            CONF_MODEL: model,
            CONF_MAX_TOKENS: 513,
            CONF_REASONING_EFFORT: "high",
            CONF_SAFE_PROMPT: True,
            CONF_TEMPERATURE: 0.35,
        },
    )
    return model


@pytest.fixture
def media_directory(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Make local synthetic files resolvable through HA's media source."""
    hass.config.media_dirs["local"] = str(tmp_path)
    return tmp_path


def _attachment(filename: str, mime_type: str) -> dict[str, str]:
    return {
        "media_content_id": f"media-source://media_source/local/{filename}",
        "media_content_type": mime_type,
    }


def _reply(text: str) -> aclosing[AsyncGenerator[CompletionEvent]]:
    return aclosing(
        event_stream([completion_event(content=text, finish_reason="stop")])
    )


async def _evaluate(
    hass: HomeAssistant,
    *,
    question_id: str = "warm",
    state: JsonValueType = "The room is warm.",
    attachments: list[dict[str, str]] | None = None,
) -> ai_task.EvaluationTaskResult:
    return await ai_task.async_evaluate(
        hass,
        entity_id=ENTITY_ID,
        task_name="Evaluate the room",
        state=state,
        questions={question_id: QUESTIONS[question_id]},
        attachments=attachments,
    )


async def test_evaluate_action_serializes_native_answers(
    hass: HomeAssistant,
    configured_model: str,
    mock_init_component: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The preferred entity answers mixed questions through the real SDK wire shape."""
    hass.data[ai_task.const.DATA_PREFERENCES].async_set_preferences(
        evaluate_entity_id=ENTITY_ID
    )
    evidence = {"temperature_c": 27, "label": "private synthetic room label"}
    result_json = json.dumps(
        {
            "warm": 0.75,
            "properties": {"too_warm": 0.5, "properties": 0.5},
            "warmth": [0.125, 0.25, 0.625],
        }
    )
    bodies = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        chunks = [
            completion_event(
                content=[
                    ThinkChunk(
                        thinking=[TextChunk(text="private synthetic reasoning")]
                    ),
                    TextChunk(text=result_json[:25]),
                ]
            ),
            completion_event(content=result_json[25:], finish_reason="stop"),
        ]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(
                f"data: {chunk.data.model_dump_json()}\n\n" for chunk in chunks
            )
            + "data: [DONE]\n\n",
        )

    caplog.set_level(logging.DEBUG, logger="custom_components.mistral_conversation")
    caplog.set_level(
        logging.DEBUG, logger="homeassistant.components.conversation.chat_log"
    )
    async with (
        httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client,
        Mistral(api_key="test-api-key", async_client=http_client) as client,
    ):
        mock_init_component.chat.stream_async.side_effect = client.chat.stream_async
        response = await hass.services.async_call(
            ai_task.DOMAIN,
            "evaluate",
            {
                "task_name": "Evaluate the room",
                "state": evidence,
                "questions": QUESTIONS,
            },
            blocking=True,
            return_response=True,
        )

    assert response == {
        "answers": {
            "warm": {"type": "noul", "noul": 0.75},
            "properties": {
                "type": "choice",
                "choice": "properties",
                "probabilities": {"properties": 0.5, "too_warm": 0.5},
            },
            "warmth": {
                "type": "score",
                "score": 1.5,
                "probabilities": [0.125, 0.25, 0.625],
            },
        }
    }
    (body,) = bodies
    assert body["model"] == configured_model
    assert body["max_tokens"] == 513
    assert body["temperature"] == 0.35
    assert body["safe_prompt"] is True
    assert body["reasoning_effort"] == "high"
    assert not body.get("tools")
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert json.loads(body["messages"][1]["content"]) == {
        "state": evidence,
        "questions": QUESTIONS,
    }
    assert body["response_format"]["type"] == "json_schema"
    json_schema = body["response_format"]["json_schema"]
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    assert set(schema["required"]) == set(QUESTIONS)
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["properties"]["properties"]) == {
        "properties",
        "too_warm",
    }
    assert schema["properties"]["warmth"]["minItems"] == 3
    assert schema["properties"]["warmth"]["maxItems"] == 3
    assert mock_init_component.chat.stream_async.await_args.kwargs["timeout_ms"] == (
        REQUEST_TIMEOUT_MS
    )
    assert "private synthetic" not in caplog.text


@pytest.mark.parametrize(
    ("question_id", "state", "answer"),
    [
        ("warm", False, 0),
        ("properties", 0, {"properties": 0, "too_warm": 1}),
        ("warmth", "", [0, 0, 1]),
    ],
)
async def test_evaluate_single_question_preserves_false_valued_state(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    question_id: str,
    state: JsonValueType,
    answer: JsonValueType,
) -> None:
    """False, zero and empty strings are evidence when supplied explicitly."""
    mock_init_component.chat.stream_async.return_value = _reply(
        json.dumps({question_id: answer})
    )
    result = await _evaluate(hass, question_id=question_id, state=state)

    assert set(result.answers) == {question_id}
    assert result.answers[question_id].type == QUESTIONS[question_id]["type"]
    messages = mock_init_component.chat.stream_async.await_args.kwargs["messages"]
    sent_state = json.loads(messages[-1].content)["state"]
    assert sent_state == state
    assert type(sent_state) is type(state)


async def test_evaluate_accepts_attachments_only(
    hass: HomeAssistant,
    media_directory: Path,
    mock_init_component: MagicMock,
) -> None:
    """HA resolves attachments and Mistral receives the supported media chunks."""
    Image.new("RGB", (2, 2), color="blue").save(media_directory / "room.png")
    (media_directory / "room.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    mock_init_component.chat.stream_async.return_value = _reply('{"warm": 0.5}')

    result = await _evaluate(
        hass,
        state=None,
        attachments=[
            _attachment("room.png", "image/png"),
            _attachment("room.pdf", "application/pdf"),
        ],
    )

    assert result.answers == {"warm": ai_task.NoulAnswer(noul=0.5)}
    messages = mock_init_component.chat.stream_async.await_args.kwargs["messages"]
    evidence, image, document = messages[-1].content
    assert json.loads(evidence.text)["state"] is None
    assert isinstance(image, ImageURLChunk)
    assert image.image_url.startswith("data:image/png;base64,")
    assert isinstance(document, DocumentURLChunk)
    assert document.document_url.startswith("data:application/pdf;base64,")


@pytest.mark.parametrize(
    ("filename", "mime_type", "expected_error"),
    [
        ("room.png", "image/png", "model_no_vision"),
        ("room.pdf", "application/pdf", "model_no_documents"),
        ("room.pdf", "application/pdf", "attachment_too_large"),
        ("room.pdf", "application/pdf", "too_many_attachments"),
    ],
)
async def test_evaluation_media_guards_prevent_provider_calls(
    hass: HomeAssistant,
    media_directory: Path,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    model: MistralModel,
    filename: str,
    mime_type: str,
    expected_error: str,
) -> None:
    """Known model capabilities and attachment bounds also apply to evaluation."""
    path = media_directory / filename
    path.write_bytes(b"synthetic attachment")
    attachments = [_attachment(filename, mime_type)]
    if expected_error.startswith("model_"):
        mock_config_entry.runtime_data.async_set_updated_data(
            [replace(model, vision=False, ocr=False)]
        )
    elif expected_error == "attachment_too_large":
        with path.open("wb") as file:
            file.truncate(MAX_ATTACHMENT_BYTES + 1)
    else:
        attachments *= MAX_ATTACHMENTS + 1

    with pytest.raises(HomeAssistantError) as raised:
        await _evaluate(hass, state=None, attachments=attachments)

    assert raised.value.translation_key == expected_error
    mock_init_component.chat.stream_async.assert_not_awaited()


@pytest.mark.parametrize(
    ("state", "expected_error"),
    [(None, "evaluate_missing_input"), (float("inf"), "evaluation_state_invalid")],
)
async def test_evaluate_rejects_missing_or_invalid_state(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    state: JsonValueType,
    expected_error: str,
) -> None:
    """A task without input or with non-JSON state never contacts Mistral."""
    with pytest.raises(ServiceValidationError) as raised:
        await _evaluate(hass, state=state)
    assert raised.value.translation_key == expected_error
    mock_init_component.chat.stream_async.assert_not_awaited()


@pytest.mark.parametrize(
    ("first_probability", "last_probability"), [(0.059, 0.94), (0.064, 0.937)]
)
async def test_evaluate_normalizes_rounding_at_both_boundaries(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    first_probability: float,
    last_probability: float,
) -> None:
    """Only rounding tolerance is accepted before computing the expected index."""
    mock_init_component.chat.stream_async.return_value = _reply(
        json.dumps({"warmth": [first_probability, 0, last_probability]})
    )
    result = await _evaluate(hass, question_id="warmth")
    answer = result.answers["warmth"]
    assert isinstance(answer, ai_task.ScoreAnswer)
    assert sum(answer.probabilities) == pytest.approx(1)
    assert answer.score == pytest.approx(
        2 * last_probability / (first_probability + last_probability)
    )


@pytest.mark.parametrize(
    ("question_id", "text"),
    [
        ("warm", "invalid synthetic JSON"),
        ("warm", '{"warm":'),
        ("warm", "[0.5]"),
        ("warm", "{}"),
        ("warm", '{"warm": 0.5, "extra": 0.5}'),
        ("warm", '{"warm": 0.5, "warm": 0.9}'),
        ("warm", '{"warm": "0.5"}'),
        ("warm", '{"warm": true}'),
        ("warm", '{"warm": NaN}'),
        ("warm", '{"warm": 1e999}'),
        ("warm", '{"warm": -0.1}'),
        ("warm", '{"warm": 1.1}'),
        ("warm", '{"warm": 1.00000000000000001}'),
        ("properties", '{"properties": {"properties": 1}}'),
        ("properties", '{"properties": {"properties": 1, "too_warm": 0, "extra": 0}}'),
        (
            "properties",
            '{"properties": {"properties": 1, "properties": 0, "too_warm": 0}}',
        ),
        ("warmth", '{"warmth": [0.5, 0.5]}'),
        ("warmth", '{"warmth": [0.4, 0, 0.5989]}'),
        ("warmth", '{"warmth": [0.4, 0, 0.6011]}'),
    ],
)
async def test_evaluate_rejects_invalid_provider_answers(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    question_id: str,
    text: str,
) -> None:
    """The action fails rather than returning partial or ambiguous answers."""
    mock_init_component.chat.stream_async.return_value = _reply(text)
    with pytest.raises(HomeAssistantError) as raised:
        await _evaluate(hass, question_id=question_id)
    assert raised.value.translation_key == "evaluation_response_invalid"
    assert raised.value.translation_placeholders is None
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("events", "expected_error"),
    [
        ([], "empty_response"),
        ([completion_event(finish_reason="error")], "stream_error"),
        (
            [completion_event(content='{"warm": 0.5}', finish_reason="length")],
            "evaluation_response_invalid",
        ),
        (
            [
                completion_event(
                    tool_calls=[
                        ToolCall(
                            function=FunctionCall(name="HassTurnOn", arguments="{}")
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ],
            "evaluation_response_invalid",
        ),
    ],
)
async def test_evaluate_rejects_failed_streams_and_tools(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    events: list[CompletionEvent],
    expected_error: str,
) -> None:
    """Empty, truncated and unexpected tool responses cannot become evaluations."""
    mock_init_component.chat.stream_async.return_value = aclosing(event_stream(events))
    with pytest.raises(HomeAssistantError) as raised:
        await _evaluate(hass)
    assert raised.value.translation_key == expected_error
    mock_init_component.chat.stream_async.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "expected_error", "available", "refresh"),
    [
        (
            mistral_error(401, "private provider payload"),
            "api_authentication_error",
            True,
            True,
        ),
        (mistral_error(429, "private provider payload"), "api_rate_limit", True, False),
        (
            httpx2.ConnectError("private provider payload"),
            "api_connection_error",
            False,
            False,
        ),
        (httpx2.ReadTimeout("private provider payload"), "api_timeout", False, False),
        (mistral_error(400, "private provider payload"), "api_error", True, False),
    ],
)
async def test_evaluate_classifies_provider_errors_without_echoing_input(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_error: str,
    available: bool,
    refresh: bool,
) -> None:
    """Provider errors preserve coordinator semantics without exposing the payload."""
    coordinator = mock_config_entry.runtime_data
    mock_init_component.chat.stream_async.side_effect = error
    with (
        patch.object(
            coordinator, "async_request_refresh", new_callable=AsyncMock
        ) as request_refresh,
        pytest.raises(HomeAssistantError) as raised,
    ):
        await _evaluate(hass)

    assert raised.value.translation_key == expected_error
    assert raised.value.translation_placeholders == {
        "message": "Evaluation request failed"
    }
    assert raised.value.__suppress_context__
    assert "private provider payload" not in str(raised.value)
    assert "private provider payload" not in caplog.text
    assert coordinator.last_update_success is available
    assert request_refresh.await_count == int(refresh)


@pytest.mark.parametrize("unload", [False, True], ids=["caller cancellation", "unload"])
async def test_evaluation_cancellation_closes_provider_stream(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    unload: bool,
) -> None:
    """The SDK response closes before the caller or unload returns."""
    started = asyncio.Event()
    closed = asyncio.Event()

    class BlockingResponse(httpx2.AsyncByteStream):
        async def __aiter__(self) -> AsyncGenerator[bytes]:
            started.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self) -> None:
            closed.set()

    async def close_client(*args: object) -> None:
        assert closed.is_set()

    mock_init_component.__aexit__.side_effect = close_client
    response = httpx2.Response(200, stream=BlockingResponse())
    mock_init_component.chat.stream_async.return_value = EventStreamAsync(
        response, CompletionEvent.model_validate_json
    )
    request = asyncio.create_task(_evaluate(hass))
    async with asyncio.timeout(5):
        await started.wait()
        if unload:
            assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        else:
            request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    assert closed.is_set()
    assert response.is_closed
    if not unload:
        mock_init_component.chat.stream_async.return_value = _reply('{"warm": 1}')
        result = await _evaluate(hass)
        assert result.answers == {"warm": ai_task.NoulAnswer(noul=1)}
