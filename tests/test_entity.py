"""Tests for typed messages, streaming, attachments, and runtime errors."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.components import conversation
from homeassistant.components.llm import LLMTools
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm
from mistralai.client.errors import NoResponseError
from mistralai.client.models import (
    AssistantMessage,
    FunctionCall,
    ImageURLChunk,
    SystemMessage,
    TextChunk,
    ThinkChunk,
    ToolCall,
    ToolMessage,
    UsageInfo,
    UserMessage,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    MAX_ATTACHMENTS,
    MAX_TOOLS,
    MistralModel,
)
from custom_components.mistral_conversation.conversation import (
    MistralConversationEntity,
)
from custom_components.mistral_conversation.entity import (
    MistralNativeContent,
    MistralStreamState,
    _convert_content_to_message,
    _format_tool,
    _transform_stream,
    async_prepare_attachments,
)

from .helpers import completion_event, event_stream, mistral_error


def _chat_log(hass: HomeAssistant) -> conversation.ChatLog:
    """Create a minimal valid provider chat log."""
    return conversation.ChatLog(
        hass,
        "conversation-id",
        content=[
            conversation.SystemContent(content="System instructions"),
            conversation.UserContent(content="Hello"),
        ],
    )


def _entity(hass: HomeAssistant) -> MistralConversationEntity:
    """Return the initialized conversation entity."""
    agent = conversation.agent_manager.async_get_agent(
        hass, "conversation.mistral_conversation"
    )
    assert isinstance(agent, MistralConversationEntity)
    return agent


def test_convert_all_chat_log_roles() -> None:
    """Every Home Assistant chat-log role has a typed SDK equivalent."""
    system = _convert_content_to_message(conversation.SystemContent(content="System"))
    user = _convert_content_to_message(conversation.UserContent(content="User"))
    assistant = _convert_content_to_message(
        conversation.AssistantContent(
            agent_id="agent",
            content="Assistant",
            tool_calls=[
                llm.ToolInput(
                    id="call-id",
                    tool_name="GetState",
                    tool_args={"name": "Kitchen"},
                )
            ],
        )
    )
    tool = _convert_content_to_message(
        conversation.ToolResultContent(
            agent_id="agent",
            tool_call_id="call-id",
            tool_name="GetState",
            tool_result={"state": "on"},
        )
    )

    assert isinstance(system, SystemMessage)
    assert system.content == "System"
    assert isinstance(user, UserMessage)
    assert user.content == "User"
    assert isinstance(assistant, AssistantMessage)
    assert assistant.content == "Assistant"
    assert assistant.tool_calls[0].function.name == "GetState"
    assert assistant.tool_calls[0].function.arguments == '{"name":"Kitchen"}'
    assert isinstance(tool, ToolMessage)
    assert tool.tool_call_id == "call-id"
    assert tool.content == '{"state":"on"}'


def test_convert_empty_and_external_content() -> None:
    """Empty messages and provider-external tool calls are not replayed."""
    assert _convert_content_to_message(conversation.SystemContent(content="")) is None
    assert _convert_content_to_message(conversation.UserContent(content="")) is None
    assert (
        _convert_content_to_message(
            conversation.AssistantContent(
                agent_id="agent",
                tool_calls=[
                    llm.ToolInput(
                        id="external",
                        tool_name="server_tool",
                        tool_args={},
                        external=True,
                    )
                ],
            )
        )
        is None
    )


def test_reasoning_is_replayed_with_signature() -> None:
    """The complete signed ThinkChunk is retained across conversation turns."""
    native = MistralNativeContent()
    native.add_chunk(
        ThinkChunk(
            thinking=[TextChunk(text="Reasoning")],
            signature="signed",
            closed=True,
        )
    )
    message = _convert_content_to_message(
        conversation.AssistantContent(
            agent_id="agent",
            content="Answer",
            thinking_content="Reasoning",
            native=native,
        )
    )

    assert isinstance(message, AssistantMessage)
    assert isinstance(message.content, list)
    assert isinstance(message.content[0], ThinkChunk)
    assert message.content[0].signature == "signed"
    assert message.content[0].closed is True
    assert isinstance(message.content[1], TextChunk)
    assert message.content[1].text == "Answer"


def test_format_tool_preserves_schema() -> None:
    """Home Assistant tool schemas and descriptions reach Mistral intact."""
    tool = MagicMock()
    tool.name = "SetMode"
    tool.description = "Set a mode"
    tool.parameters = vol.Schema({vol.Required("mode"): vol.In(["home", "away"])})

    formatted = _format_tool(tool, None)

    assert formatted.function.name == "SetMode"
    assert formatted.function.description == "Set a mode"
    assert formatted.function.parameters["type"] == "object"
    assert "mode" in formatted.function.parameters["properties"]
    assert formatted.function.strict is False


async def test_transform_text_reasoning_usage_and_native(
    hass: HomeAssistant,
) -> None:
    """Streaming text, thinking, usage, and replay metadata are all retained."""
    chat_log = _chat_log(hass)
    chat_log.async_trace = MagicMock()
    state = MistralStreamState()
    deltas = [
        delta
        async for delta in _transform_stream(
            chat_log,
            event_stream(
                [
                    completion_event(
                        content=[
                            ThinkChunk(
                                thinking=[TextChunk(text="Think ")],
                                signature="sig",
                                closed=False,
                            ),
                            TextChunk(text="Answer "),
                        ]
                    ),
                    completion_event(content="continued"),
                    completion_event(
                        finish_reason="stop",
                        usage=UsageInfo(
                            prompt_tokens=12,
                            completion_tokens=7,
                            total_tokens=19,
                        ),
                    ),
                ]
            ),
            state,
        )
    ]

    assert deltas[0] == {"role": "assistant"}
    assert {"thinking_content": "Think "} in deltas
    assert {"content": "Answer "} in deltas
    assert {"content": "continued"} in deltas
    native = next(delta["native"] for delta in deltas if "native" in delta)
    assert isinstance(native, MistralNativeContent)
    assert native.signature == "sig"
    assert state.has_output
    chat_log.async_trace.assert_called_with(
        {"stats": {"input_tokens": 12, "output_tokens": 7}}
    )


async def test_transform_fragmented_parallel_tool_calls(
    hass: HomeAssistant,
) -> None:
    """Fragmented and parallel provider calls become ordered HA ToolInputs."""
    chat_log = _chat_log(hass)
    state = MistralStreamState()
    deltas = [
        delta
        async for delta in _transform_stream(
            chat_log,
            event_stream(
                [
                    completion_event(
                        tool_calls=[
                            ToolCall(
                                id="two",
                                index=1,
                                function=FunctionCall(
                                    name="Second",
                                    arguments={},
                                ),
                            ),
                            ToolCall(
                                id="one",
                                index=0,
                                function=FunctionCall(
                                    name="First",
                                    arguments='{"name":',
                                ),
                            ),
                        ]
                    ),
                    completion_event(
                        tool_calls=[
                            ToolCall(
                                id="null",
                                index=0,
                                function=FunctionCall(
                                    name="",
                                    arguments='"Kitchen"}',
                                ),
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                ]
            ),
            state,
        )
    ]

    tool_inputs = deltas[-1]["tool_calls"]
    assert [tool.tool_name for tool in tool_inputs] == ["First", "Second"]
    assert tool_inputs[0].tool_args == {"name": "Kitchen"}
    assert state.has_output


async def test_transform_invalid_tool_arguments(
    hass: HomeAssistant,
) -> None:
    """Invalid provider JSON becomes a translated Home Assistant error."""
    chat_log = _chat_log(hass)
    stream = _transform_stream(
        chat_log,
        event_stream(
            [
                completion_event(
                    tool_calls=[
                        ToolCall(
                            id="call",
                            index=0,
                            function=FunctionCall(
                                name="Broken",
                                arguments="{",
                            ),
                        )
                    ]
                )
            ]
        ),
        MistralStreamState(),
    )

    with pytest.raises(HomeAssistantError) as error:
        _ = [delta async for delta in stream]

    assert error.value.translation_key == "tool_call_invalid"


async def test_transform_provider_finish_error(
    hass: HomeAssistant,
) -> None:
    """An explicit stream error finish reason is not mistaken for success."""
    with pytest.raises(HomeAssistantError) as error:
        _ = [
            delta
            async for delta in _transform_stream(
                _chat_log(hass),
                event_stream([completion_event(finish_reason="error")]),
                MistralStreamState(),
            )
        ]

    assert error.value.translation_key == "stream_error"


async def test_prepare_image_and_pdf_attachments(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Supported local files become typed base64 data-URL chunks."""
    image = tmp_path / "image.png"
    image.write_bytes(b"\x89PNG")
    document = tmp_path / "document.pdf"
    document.write_bytes(b"%PDF")

    prepared = await async_prepare_attachments(
        hass,
        [
            (image, "image/png; charset=binary"),
            (document, None),
        ],
    )

    assert isinstance(prepared[0], ImageURLChunk)
    assert str(prepared[0].image_url).startswith("data:image/png;base64,")
    assert prepared[1].document_name == "document.pdf"
    assert prepared[1].document_url.startswith("data:application/pdf;base64,")


@pytest.mark.parametrize(
    ("filename", "mime_type", "translation_key"),
    [
        ("missing.png", "image/png", "attachment_not_found"),
        ("unsupported.txt", "text/plain", "attachment_type_unsupported"),
    ],
)
async def test_prepare_attachment_errors(
    hass: HomeAssistant,
    tmp_path: Path,
    filename: str,
    mime_type: str,
    translation_key: str,
) -> None:
    """Missing and unsupported files fail before any provider request."""
    path = tmp_path / filename
    if filename != "missing.png":
        path.write_text("unsupported")

    with pytest.raises(HomeAssistantError) as error:
        await async_prepare_attachments(hass, [(path, mime_type)])

    assert error.value.translation_key == translation_key


async def test_prepare_attachment_size_and_count_limits(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local encoding is bounded by per-file and per-request limits."""
    image = tmp_path / "image.png"
    image.write_bytes(b"ab")
    monkeypatch.setattr(
        "custom_components.mistral_conversation.entity.MAX_ATTACHMENT_BYTES",
        1,
    )

    with pytest.raises(HomeAssistantError) as error:
        await async_prepare_attachments(hass, [(image, "image/png")])
    assert error.value.translation_key == "attachment_too_large"

    attachments = [
        (tmp_path / f"{index}.png", "image/png") for index in range(MAX_ATTACHMENTS + 1)
    ]
    with pytest.raises(HomeAssistantError) as error:
        await async_prepare_attachments(hass, attachments)
    assert error.value.translation_key == "too_many_attachments"


async def test_conversation_success_and_request_parameters(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """A full Home Assistant conversation streams a typed Mistral answer."""
    mock_init_component.chat.stream_async.return_value = event_stream(
        [
            completion_event(content="Hello "),
            completion_event(
                content="from Mistral",
                finish_reason="stop",
                usage=UsageInfo(
                    prompt_tokens=10,
                    completion_tokens=3,
                    total_tokens=13,
                ),
            ),
        ]
    )
    with patch(
        "homeassistant.components.llm.async_get_tools",
        new_callable=AsyncMock,
        return_value=LLMTools(tools=[]),
    ):
        result = await conversation.async_converse(
            hass,
            "Hello",
            None,
            Context(),
            agent_id="conversation.mistral_conversation",
        )

    assert result.response.response_type is intent.IntentResponseType.ACTION_DONE
    assert result.response.speech["plain"]["speech"] == "Hello from Mistral"
    call = mock_init_component.chat.stream_async.await_args
    assert call.kwargs["model"] == "mistral-small-latest"
    assert call.kwargs["max_tokens"] == 2048
    assert call.kwargs["temperature"] == 0.2
    assert call.kwargs["reasoning_effort"] == "none"
    assert call.kwargs["safe_prompt"] is False
    assert call.kwargs["prompt_cache_key"] == result.conversation_id
    assert isinstance(call.kwargs["messages"][0], SystemMessage)
    assert isinstance(call.kwargs["messages"][1], UserMessage)


async def test_conversation_function_call_round_trip(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """HA tools execute and their typed result is sent in a follow-up request."""
    mock_tool = AsyncMock()
    mock_tool.name = "GetKitchen"
    mock_tool.description = "Get kitchen state"
    mock_tool.parameters = vol.Schema(
        {vol.Required("name", description="Entity name"): str}
    )
    mock_tool.async_call.return_value = {"state": "on"}

    mock_init_component.chat.stream_async.side_effect = [
        event_stream(
            [
                completion_event(
                    tool_calls=[
                        ToolCall(
                            id="call-id",
                            index=0,
                            function=FunctionCall(
                                name="GetKitchen",
                                arguments='{"name":"Kitchen"}',
                            ),
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ]
        ),
        event_stream(
            [
                completion_event(
                    content="The kitchen is on.",
                    finish_reason="stop",
                )
            ]
        ),
    ]

    with patch(
        "homeassistant.components.llm.async_get_tools",
        new_callable=AsyncMock,
        return_value=LLMTools(tools=[mock_tool]),
    ):
        result = await conversation.async_converse(
            hass,
            "Check the kitchen",
            None,
            Context(),
            agent_id="conversation.mistral_conversation",
        )

    assert result.response.speech["plain"]["speech"] == "The kitchen is on."
    assert mock_init_component.chat.stream_async.await_count == 2
    second_messages = mock_init_component.chat.stream_async.await_args_list[1].kwargs[
        "messages"
    ]
    assert any(isinstance(message, AssistantMessage) for message in second_messages)
    tool_message = next(
        message for message in second_messages if isinstance(message, ToolMessage)
    )
    assert tool_message.tool_call_id == "call-id"
    assert tool_message.content == '{"state":"on"}'
    mock_tool.async_call.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "translation_key", "available"),
    [
        (mistral_error(429, "limited"), "api_rate_limit", True),
        (mistral_error(400, "bad input"), "api_error", True),
        (mistral_error(503, "offline"), "api_connection_error", False),
        (NoResponseError("no response"), "api_connection_error", False),
        (TimeoutError("slow"), "api_timeout", False),
    ],
)
async def test_runtime_api_error_mapping(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
    error: BaseException,
    translation_key: str,
    available: bool,
) -> None:
    """Expected runtime failures update availability and use translated errors."""
    entity = _entity(hass)
    mock_init_component.chat.stream_async.side_effect = error

    with pytest.raises(HomeAssistantError) as raised:
        await entity._async_handle_chat_log(_chat_log(hass))

    assert raised.value.translation_key == translation_key
    assert mock_config_entry.runtime_data.last_update_success is available


async def test_runtime_auth_error_requests_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """A chat-endpoint auth failure asks the coordinator to confirm reauth."""
    entity = _entity(hass)
    mock_init_component.chat.stream_async.side_effect = mistral_error(401, "bad key")
    with (
        patch.object(
            mock_config_entry.runtime_data,
            "async_request_refresh",
            new_callable=AsyncMock,
        ) as refresh,
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity._async_handle_chat_log(_chat_log(hass))

    assert raised.value.translation_key == "api_authentication_error"
    refresh.assert_awaited_once()


async def test_empty_response_and_iteration_guard(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """Empty streams and impossible tool loops cannot report false success."""
    entity = _entity(hass)
    mock_init_component.chat.stream_async.return_value = event_stream([])

    with pytest.raises(HomeAssistantError) as empty:
        await entity._async_handle_chat_log(_chat_log(hass))
    assert empty.value.translation_key == "empty_response"

    with pytest.raises(HomeAssistantError) as iterations:
        await entity._async_handle_chat_log(_chat_log(hass), max_iterations=0)
    assert iterations.value.translation_key == "max_tool_iterations"


@pytest.mark.parametrize(
    ("model", "known", "reasoning", "attachments", "tools", "translation_key"),
    [
        (
            MistralModel(id="limited"),
            True,
            "none",
            [],
            [object()],
            "model_no_tools",
        ),
        (
            MistralModel(id="limited", function_calling=True),
            True,
            "high",
            [],
            [],
            "model_no_reasoning",
        ),
        (
            MistralModel(id="limited"),
            True,
            "none",
            [(Path("image.png"), "image/png")],
            [],
            "model_no_vision",
        ),
        (
            MistralModel(id="limited"),
            True,
            "none",
            [(Path("document.pdf"), "application/pdf")],
            [],
            "model_no_documents",
        ),
    ],
)
async def test_runtime_capability_guards(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    model: MistralModel,
    known: bool,
    reasoning: str,
    attachments: list[tuple[Path, str]],
    tools: list[object],
    translation_key: str,
) -> None:
    """Known provider capabilities are enforced again at request time."""
    entity = _entity(hass)
    chat_log = _chat_log(hass)
    chat_log.llm_api = SimpleNamespace(tools=tools)

    with pytest.raises(HomeAssistantError) as error:
        entity._validate_capabilities(
            model,
            known,
            chat_log,
            reasoning,
            attachments,
        )

    assert error.value.translation_key == translation_key


async def test_unknown_model_capabilities_are_permissive(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """Custom model IDs are not rejected based on absent model-card data."""
    entity = _entity(hass)
    chat_log = _chat_log(hass)
    chat_log.llm_api = SimpleNamespace(tools=[object()])

    entity._validate_capabilities(
        MistralModel(id="custom"),
        False,
        chat_log,
        "xhigh",
        [(Path("image.png"), "image/png")],
    )


async def test_tool_count_limit(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """The documented provider tool-count maximum is checked locally."""
    entity = _entity(hass)
    chat_log = _chat_log(hass)
    chat_log.llm_api = SimpleNamespace(tools=[object()] * (MAX_TOOLS + 1))

    with pytest.raises(HomeAssistantError) as error:
        entity._validate_capabilities(
            MistralModel(id="custom"),
            False,
            chat_log,
            "none",
            [],
        )

    assert error.value.translation_key == "too_many_tools"


async def test_entity_features_and_supported_languages(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
) -> None:
    """Default Assist configuration advertises control and all languages."""
    entity = _entity(hass)
    state = hass.states.get("conversation.mistral_conversation")

    assert entity.supported_languages == "*"
    assert state is not None
    assert (
        state.attributes["supported_features"]
        == conversation.ConversationEntityFeature.CONTROL
    )
