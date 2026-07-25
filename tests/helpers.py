"""Provider-model helpers used by the Mistral integration tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable

import httpx
from mistralai.client.errors import MistralError
from mistralai.client.models import (
    CompletionChunk,
    CompletionEvent,
    CompletionResponseStreamChoice,
    DeltaMessage,
    ToolCall,
    UsageInfo,
)


def completion_event(
    *,
    content: object = None,
    tool_calls: list[ToolCall] | None = None,
    finish_reason: str | None = None,
    usage: UsageInfo | None = None,
) -> CompletionEvent:
    """Create one generated-SDK completion event."""
    delta_args: dict[str, object] = {}
    if content is not None:
        delta_args["content"] = content
    if tool_calls is not None:
        delta_args["tool_calls"] = tool_calls

    return CompletionEvent(
        data=CompletionChunk(
            id="completion-id",
            model="mistral-small-latest",
            choices=[
                CompletionResponseStreamChoice(
                    index=0,
                    delta=DeltaMessage(**delta_args),
                    finish_reason=finish_reason,
                )
            ],
            usage=usage,
        )
    )


async def event_stream(
    events: Iterable[CompletionEvent],
) -> AsyncGenerator[CompletionEvent]:
    """Yield provider events as an asynchronous stream."""
    for event in events:
        yield event


def mistral_error(status_code: int, message: str = "provider error") -> MistralError:
    """Construct a real SDK error with an HTTP status."""
    response = httpx.Response(
        status_code,
        request=httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions"),
    )
    return MistralError(message, response)
