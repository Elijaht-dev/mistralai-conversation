"""Pure helpers for assembling streamed Mistral tool calls."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ToolCallDecodeError(ValueError):
    """Raised when streamed tool-call data cannot be decoded."""


@dataclass(frozen=True, slots=True)
class CompletedToolCall:
    """A complete tool call ready for Home Assistant."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class _PendingToolCall:
    """A tool call while its streamed fragments are being collected."""

    index: int
    id: str = ""
    name: str = ""
    arguments_text: str = ""
    arguments_object: dict[str, Any] | None = None

    def update(
        self,
        *,
        call_id: str | None,
        name: str | None,
        arguments: str | dict[str, Any] | None,
    ) -> None:
        """Merge a delta into this pending tool call."""
        if call_id and call_id != "null":
            self.id = call_id
        if name:
            self.name = name

        if isinstance(arguments, dict):
            if self.arguments_object is None:
                self.arguments_object = {}
            self.arguments_object.update(arguments)
        elif isinstance(arguments, str):
            self.arguments_text = _merge_text_fragment(self.arguments_text, arguments)

    def complete(self, id_factory: Callable[[], str]) -> CompletedToolCall:
        """Decode and return the completed tool call."""
        if not self.name:
            raise ToolCallDecodeError(
                f"Mistral returned tool call {self.index} without a function name"
            )

        if self.arguments_object is not None:
            arguments = self.arguments_object
        else:
            raw_arguments = self.arguments_text.strip()
            if not raw_arguments:
                arguments = {}
            else:
                try:
                    decoded = json.loads(raw_arguments)
                except json.JSONDecodeError as err:
                    raise ToolCallDecodeError(
                        f"Invalid arguments for tool {self.name}: {err}"
                    ) from err
                if not isinstance(decoded, dict):
                    raise ToolCallDecodeError(
                        f"Arguments for tool {self.name} must be a JSON object"
                    )
                arguments = decoded

        return CompletedToolCall(
            id=self.id or id_factory(),
            name=self.name,
            arguments=arguments,
        )


def _merge_text_fragment(existing: str, fragment: str) -> str:
    """Merge incremental or cumulative JSON argument text."""
    if not fragment:
        return existing
    if not existing:
        return fragment
    if fragment == existing:
        return existing
    if fragment.startswith(existing):
        return fragment
    return existing + fragment


class ToolCallAccumulator:
    """Collect tool-call fragments by their Mistral stream index."""

    def __init__(self) -> None:
        """Initialize an empty accumulator."""
        self._calls: dict[int, _PendingToolCall] = {}

    def add(
        self,
        *,
        index: int | None,
        call_id: str | None,
        name: str | None,
        arguments: str | dict[str, Any] | None,
    ) -> None:
        """Add one streamed tool-call fragment."""
        normalized_index = index if isinstance(index, int) else 0
        pending = self._calls.setdefault(
            normalized_index, _PendingToolCall(index=normalized_index)
        )
        pending.update(call_id=call_id, name=name, arguments=arguments)

    def complete(
        self, id_factory: Callable[[], str] | None = None
    ) -> list[CompletedToolCall]:
        """Return all completed calls in stream order."""
        resolved_id_factory = id_factory or _new_call_id
        return [
            self._calls[index].complete(resolved_id_factory)
            for index in sorted(self._calls)
        ]

    def __bool__(self) -> bool:
        """Return whether any tool calls have been collected."""
        return bool(self._calls)


def _new_call_id() -> str:
    """Create an ID when a provider omits one."""
    return secrets.token_hex(5)[:9]
