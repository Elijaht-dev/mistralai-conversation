"""Shared entity and message handling for Mistral AI Conversation."""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    Callable,
    Iterator,
    Sequence,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never, cast

import httpx
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import llm
from homeassistant.helpers.json import json_dumps
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from mistralai.client.errors import MistralError, NoResponseError
from mistralai.client.models import (
    AssistantMessage,
    ChatCompletionStreamRequestMessage,
    ChatCompletionStreamRequestTool,
    CompletionEvent,
    DocumentURLChunk,
    Function,
    FunctionCall,
    ImageURLChunk,
    ReferenceChunk,
    SystemMessage,
    TextChunk,
    ThinkChunk,
    Tool,
    ToolCall,
    ToolMessage,
    ToolReferenceChunk,
    UserMessage,
)
from mistralai.client.models.contentchunk import ContentChunk
from mistralai.client.models.thinkchunk import Thinking
from voluptuous_openapi import convert  # type: ignore[import-untyped]

from .const import (
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SAFE_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_CONVERSATION_OPTIONS,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    LOGGER,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    MAX_TOOL_ITERATIONS,
    MAX_TOOLS,
    REASONING_EFFORT_NONE,
    REASONING_EFFORTS,
    REQUEST_TIMEOUT_MS,
    SUPPORTED_DOCUMENT_MIME_TYPES,
    SUPPORTED_IMAGE_MIME_TYPES,
    ApiErrorKind,
    MistralModel,
    ReasoningEffort,
)
from .coordinator import MistralConfigEntry, MistralCoordinator
from .errors import api_error_message, classify_api_error
from .tool_calls import ToolCallAccumulator, ToolCallDecodeError

type MistralMessage = ChatCompletionStreamRequestMessage
type MistralTool = ChatCompletionStreamRequestTool
type MistralAttachmentChunk = ImageURLChunk | DocumentURLChunk


@dataclass(slots=True)
class MistralNativeContent:
    """Provider-native reasoning retained for replay on later turns."""

    thinking: list[Thinking] = field(default_factory=list)
    signature: str | None = None
    closed: bool | None = None

    def add_chunk(self, chunk: ThinkChunk) -> None:
        """Merge one streamed reasoning chunk."""
        self.thinking.extend(chunk.thinking)
        if isinstance(chunk.signature, str):
            self.signature = chunk.signature
        if isinstance(chunk.closed, bool):
            self.closed = chunk.closed

    def as_content_chunk(self) -> ThinkChunk | None:
        """Return a complete reasoning chunk suitable for replay."""
        if not self.thinking:
            return None
        if self.signature is not None:
            return ThinkChunk(
                thinking=self.thinking,
                signature=self.signature,
                closed=self.closed,
            )
        return ThinkChunk(thinking=self.thinking, closed=self.closed)


@dataclass(slots=True)
class MistralRequest:
    """Validated request state reused across tool-call iterations."""

    model: str
    messages: list[MistralMessage]
    tools: list[MistralTool]
    max_tokens: int
    temperature: float
    reasoning_effort: ReasoningEffort
    safe_prompt: bool
    prompt_cache_key: str | None


@dataclass(slots=True)
class MistralStreamState:
    """Observable state collected while transforming a response stream."""

    has_output: bool = False


def _format_tool(
    tool: llm.Tool,
    custom_serializer: Callable[[Any], Any] | None,
) -> Tool:
    """Format a Home Assistant LLM tool for Mistral."""
    return Tool(
        function=Function(
            name=tool.name,
            description=tool.description or None,
            parameters=convert(
                tool.parameters,
                custom_serializer=custom_serializer,
            ),
            strict=False,
        )
    )


def _assistant_message(
    content: conversation.AssistantContent,
) -> AssistantMessage | None:
    """Convert one Home Assistant assistant item to an SDK message."""
    tool_calls = [
        ToolCall(
            id=tool_call.id,
            index=index,
            function=FunctionCall(
                name=tool_call.tool_name,
                arguments=json_dumps(tool_call.tool_args),
            ),
        )
        for index, tool_call in enumerate(content.tool_calls or [])
        if not tool_call.external
    ]

    native_chunk = (
        content.native.as_content_chunk()
        if isinstance(content.native, MistralNativeContent)
        else None
    )
    if native_chunk is not None:
        content_chunks: list[ContentChunk] = [native_chunk]
        if content.content:
            content_chunks.append(TextChunk(text=content.content))
        if tool_calls:
            return AssistantMessage(content=content_chunks, tool_calls=tool_calls)
        return AssistantMessage(content=content_chunks)

    if content.content is not None and tool_calls:
        return AssistantMessage(content=content.content, tool_calls=tool_calls)
    if content.content is not None:
        return AssistantMessage(content=content.content)
    if tool_calls:
        return AssistantMessage(tool_calls=tool_calls)
    return None


def _convert_content_to_message(
    content: conversation.Content,
) -> MistralMessage | None:
    """Convert a Home Assistant chat-log item to a typed Mistral message."""
    if isinstance(content, conversation.ToolResultContent):
        return ToolMessage(
            tool_call_id=content.tool_call_id,
            name=content.tool_name,
            content=json_dumps(content.tool_result),
        )

    if isinstance(content, conversation.SystemContent):
        return SystemMessage(content=content.content) if content.content else None

    if isinstance(content, conversation.UserContent):
        if content.content or content.attachments:
            return UserMessage(content=content.content or "")
        return None

    if isinstance(content, conversation.AssistantContent):
        return _assistant_message(content)

    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="unexpected_chat_log_content",
        translation_placeholders={"type": type(content).__name__},
    )


def _thinking_text(chunk: ThinkChunk) -> Iterator[str]:
    """Yield user-visible text from a reasoning chunk."""
    for item in chunk.thinking:
        if isinstance(item, TextChunk) and item.text:
            yield item.text


async def _transform_stream(  # noqa: PLR0912
    chat_log: conversation.ChatLog,
    stream: AsyncIterable[CompletionEvent],
    state: MistralStreamState,
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Transform Mistral streaming events into Home Assistant deltas."""
    tool_calls = ToolCallAccumulator()
    native_content = MistralNativeContent()
    yield {"role": "assistant"}

    async for event in stream:
        data = event.data
        if data.usage is not None:
            chat_log.async_trace(
                {
                    "stats": {
                        "input_tokens": data.usage.prompt_tokens or 0,
                        "output_tokens": data.usage.completion_tokens or 0,
                    }
                }
            )

        if not data.choices:
            continue

        choice = data.choices[0]
        if choice.finish_reason == "error":
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="stream_error",
            )

        delta_content = choice.delta.content
        if isinstance(delta_content, str):
            if delta_content:
                state.has_output = True
                yield {"content": delta_content}
        elif isinstance(delta_content, list):
            for chunk in delta_content:
                if isinstance(chunk, TextChunk) and chunk.text:
                    state.has_output = True
                    yield {"content": chunk.text}
                elif isinstance(chunk, ThinkChunk):
                    native_content.add_chunk(chunk)
                    for thinking_text in _thinking_text(chunk):
                        state.has_output = True
                        yield {"thinking_content": thinking_text}
                elif isinstance(chunk, ReferenceChunk | ToolReferenceChunk):
                    LOGGER.debug(
                        "Ignoring non-text Mistral response chunk %s",
                        type(chunk).__name__,
                    )

        streamed_calls = choice.delta.tool_calls
        if not isinstance(streamed_calls, list):
            continue

        for streamed_call in streamed_calls:
            arguments = streamed_call.function.arguments
            if not isinstance(arguments, str | dict):
                arguments = None

            tool_calls.add(
                index=streamed_call.index,
                call_id=streamed_call.id,
                name=streamed_call.function.name,
                arguments=arguments,
            )

    if native_content.as_content_chunk() is not None:
        yield {"native": native_content}

    if not tool_calls:
        return

    try:
        completed_calls = tool_calls.complete()
    except ToolCallDecodeError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="tool_call_invalid",
            translation_placeholders={"message": str(err)},
        ) from err

    state.has_output = True
    yield {
        "tool_calls": [
            llm.ToolInput(
                id=tool_call.id,
                tool_name=tool_call.name,
                tool_args=tool_call.arguments,
            )
            for tool_call in completed_calls
        ]
    }


def _normalize_mime_type(path: Path, supplied_mime_type: str | None) -> str | None:
    """Normalize an attachment MIME type."""
    mime_type = supplied_mime_type or mimetypes.guess_type(path)[0]
    if mime_type is None:
        return None
    normalized = mime_type.partition(";")[0].strip().casefold()
    if normalized == "image/jpg":
        return "image/jpeg"
    return normalized


def _attachment_kind(path: Path, mime_type: str | None) -> str | None:
    """Return the supported attachment kind without reading the file."""
    normalized = _normalize_mime_type(path, mime_type)
    if normalized in SUPPORTED_IMAGE_MIME_TYPES:
        return "image"
    if normalized in SUPPORTED_DOCUMENT_MIME_TYPES:
        return "document"
    return None


async def async_prepare_attachments(
    hass: HomeAssistant,
    attachments: list[tuple[Path, str | None]],
) -> list[MistralAttachmentChunk]:
    """Validate and encode image and PDF attachments for Mistral."""
    if len(attachments) > MAX_ATTACHMENTS:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="too_many_attachments",
            translation_placeholders={"maximum": str(MAX_ATTACHMENTS)},
        )

    def prepare() -> list[MistralAttachmentChunk]:
        prepared: list[MistralAttachmentChunk] = []

        for file_path, supplied_mime_type in attachments:
            if not file_path.is_file():
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="attachment_not_found",
                    translation_placeholders={"filename": file_path.name},
                )

            mime_type = _normalize_mime_type(file_path, supplied_mime_type)
            kind = _attachment_kind(file_path, mime_type)
            if kind is None or mime_type is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="attachment_type_unsupported",
                    translation_placeholders={
                        "filename": file_path.name,
                        "mime_type": mime_type or "unknown",
                    },
                )

            size = file_path.stat().st_size
            if size > MAX_ATTACHMENT_BYTES:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="attachment_too_large",
                    translation_placeholders={
                        "filename": file_path.name,
                        "maximum_mb": str(MAX_ATTACHMENT_BYTES // 1024 // 1024),
                    },
                )

            encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
            data_url = f"data:{mime_type};base64,{encoded}"
            if kind == "image":
                prepared.append(ImageURLChunk(image_url=data_url))
            else:
                prepared.append(
                    DocumentURLChunk(
                        document_url=data_url,
                        document_name=file_path.name,
                    )
                )

        return prepared

    return await hass.async_add_executor_job(prepare)


def _has_meaningful_content(contents: Sequence[conversation.Content]) -> bool:
    """Return whether a transformed stream produced text, reasoning, or tools."""
    return any(
        isinstance(content, conversation.AssistantContent)
        and bool(
            content.content
            or content.thinking_content
            or content.tool_calls
            or isinstance(content.native, MistralNativeContent)
        )
        for content in contents
    )


class MistralBaseEntity(CoordinatorEntity[MistralCoordinator]):
    """Base entity for Mistral-backed conversation features."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: MistralConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the entity."""
        super().__init__(entry.runtime_data)
        self.entry = entry
        self.subentry = subentry
        self.model = cast(str, subentry.data[CONF_MODEL])
        model_info, _ = self.coordinator.get_model_info(self.model)
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Mistral AI",
            model=model_info.name or model_info.id,
            model_id=self.model,
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    def _validate_capabilities(
        self,
        model: MistralModel,
        known: bool,
        chat_log: conversation.ChatLog,
        reasoning_effort: ReasoningEffort,
        attachments: list[tuple[Path, str | None]],
    ) -> None:
        """Reject requests that contradict known provider capabilities."""
        tools = chat_log.llm_api.tools if chat_log.llm_api else []
        if len(tools) > MAX_TOOLS:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="too_many_tools",
                translation_placeholders={"maximum": str(MAX_TOOLS)},
            )

        if not known:
            return
        if tools and not model.function_calling:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="model_no_tools",
                translation_placeholders={"model": self.model},
            )
        if reasoning_effort != REASONING_EFFORT_NONE and not model.reasoning:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="model_no_reasoning",
                translation_placeholders={"model": self.model},
            )

        for path, mime_type in attachments:
            attachment_kind = _attachment_kind(path, mime_type)
            if attachment_kind == "image" and not model.vision:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="model_no_vision",
                    translation_placeholders={"model": self.model},
                )
            if attachment_kind == "document" and not model.supports_documents:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="model_no_documents",
                    translation_placeholders={"model": self.model},
                )

    async def _async_build_request(
        self, chat_log: conversation.ChatLog
    ) -> MistralRequest:
        """Build and validate the first provider request."""
        if not chat_log.content or not isinstance(
            chat_log.content[0], conversation.SystemContent
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="system_message_not_found",
            )

        options = DEFAULT_CONVERSATION_OPTIONS | dict(self.subentry.data)
        messages = [
            message
            for content in chat_log.content
            if (message := _convert_content_to_message(content)) is not None
        ]

        last_content = chat_log.content[-1]
        attachments: list[tuple[Path, str | None]] = []
        if isinstance(last_content, conversation.UserContent):
            attachments = [
                (attachment.path, attachment.mime_type)
                for attachment in last_content.attachments or []
            ]

        raw_reasoning_effort = options[CONF_REASONING_EFFORT]
        reasoning_effort: ReasoningEffort = (
            raw_reasoning_effort
            if raw_reasoning_effort in REASONING_EFFORTS
            else REASONING_EFFORT_NONE
        )
        model_info, model_known = self.coordinator.get_model_info(self.model)
        self._validate_capabilities(
            model_info,
            model_known,
            chat_log,
            reasoning_effort,
            attachments,
        )

        max_tokens = cast(int, options[CONF_MAX_TOKENS])
        if (
            model_known
            and model_info.max_context_length is not None
            and max_tokens > model_info.max_context_length
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="max_tokens_context",
                translation_placeholders={
                    "maximum": str(model_info.max_context_length),
                    "model": self.model,
                },
            )

        if attachments:
            if not messages or not isinstance(messages[-1], UserMessage):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="user_message_not_found",
                )
            attachment_chunks = await async_prepare_attachments(self.hass, attachments)
            last_message = messages[-1]
            content_chunks: list[ContentChunk] = []
            if isinstance(last_message.content, str) and last_message.content:
                content_chunks.append(TextChunk(text=last_message.content))
            content_chunks.extend(attachment_chunks)
            messages[-1] = UserMessage(content=content_chunks)

        tools: list[MistralTool] = []
        if chat_log.llm_api:
            tools = [
                _format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]

        chat_log.async_trace(
            {
                "mistral": {
                    "model": self.model,
                    "reasoning_effort": reasoning_effort,
                    "safe_prompt": bool(options[CONF_SAFE_PROMPT]),
                    "tool_count": len(tools),
                }
            }
        )

        raw_temperature = options[CONF_TEMPERATURE]
        temperature = (
            float(raw_temperature)
            if not isinstance(raw_temperature, bool)
            and isinstance(raw_temperature, int | float)
            else DEFAULT_TEMPERATURE
        )

        return MistralRequest(
            model=self.model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            safe_prompt=bool(options[CONF_SAFE_PROMPT]),
            prompt_cache_key=chat_log.conversation_id,
        )

    async def _async_handle_api_error(self, err: BaseException) -> Never:
        """Update coordinator state and raise a translated runtime error."""
        coordinator = self.coordinator
        error_kind = classify_api_error(err)
        message = api_error_message(err)

        if error_kind is ApiErrorKind.AUTHENTICATION:
            await coordinator.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="api_authentication_error",
                translation_placeholders={"message": message},
            ) from err

        if error_kind in (ApiErrorKind.CONNECTION, ApiErrorKind.TIMEOUT):
            coordinator.mark_connection_error()
        else:
            coordinator.async_set_updated_data(coordinator.data or [])

        translation_key = {
            ApiErrorKind.CONNECTION: "api_connection_error",
            ApiErrorKind.TIMEOUT: "api_timeout",
            ApiErrorKind.RATE_LIMIT: "api_rate_limit",
        }.get(error_kind, "api_error")
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={"message": message},
        ) from err

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        max_iterations: int = MAX_TOOL_ITERATIONS,
    ) -> None:
        """Generate an answer and execute requested Home Assistant tools."""
        request = await self._async_build_request(chat_log)

        for _iteration in range(max_iterations):
            stream_state = MistralStreamState()
            try:
                stream = await self.coordinator.client.chat.stream_async(
                    model=request.model,
                    messages=request.messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    safe_prompt=request.safe_prompt,
                    prompt_cache_key=request.prompt_cache_key,
                    reasoning_effort=request.reasoning_effort,
                    tools=request.tools or None,
                    tool_choice="auto" if request.tools else None,
                    parallel_tool_calls=bool(request.tools),
                    timeout_ms=REQUEST_TIMEOUT_MS,
                )
                new_contents = [
                    content
                    async for content in chat_log.async_add_delta_content_stream(
                        self.entity_id,
                        _transform_stream(chat_log, stream, stream_state),
                    )
                ]
            except (
                MistralError,
                NoResponseError,
                httpx.HTTPError,
                TimeoutError,
            ) as err:
                await self._async_handle_api_error(err)

            if not stream_state.has_output or not _has_meaningful_content(new_contents):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="empty_response",
                )

            request.messages.extend(
                message
                for content in new_contents
                if (message := _convert_content_to_message(content)) is not None
            )

            if not chat_log.unresponded_tool_results:
                self.coordinator.async_set_updated_data(self.coordinator.data or [])
                return

        LOGGER.warning(
            "Stopped after %s Mistral tool iterations with unresolved tool calls",
            max_iterations,
        )
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="max_tool_iterations",
            translation_placeholders={"maximum": str(max_iterations)},
        )
