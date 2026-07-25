"""Tests for streamed Mistral tool-call assembly."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "mistral_conversation"
    / "tool_calls.py"
)
_SPEC = importlib.util.spec_from_file_location("mistral_tool_calls", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

ToolCallAccumulator = _MODULE.ToolCallAccumulator
ToolCallDecodeError = _MODULE.ToolCallDecodeError


class ToolCallAccumulatorTests(unittest.TestCase):
    """Test tool-call stream assembly without Home Assistant dependencies."""

    def test_fragmented_arguments(self) -> None:
        """Incremental JSON fragments are combined."""
        calls = ToolCallAccumulator()
        calls.add(
            index=0,
            call_id="call-1",
            name="HassTurnOn",
            arguments='{"name":',
        )
        calls.add(
            index=0,
            call_id=None,
            name=None,
            arguments='"Kitchen"}',
        )

        completed = calls.complete()

        self.assertEqual(completed[0].id, "call-1")
        self.assertEqual(completed[0].name, "HassTurnOn")
        self.assertEqual(completed[0].arguments, {"name": "Kitchen"})

    def test_cumulative_arguments_are_not_duplicated(self) -> None:
        """Cumulative SDK snapshots replace shorter snapshots."""
        calls = ToolCallAccumulator()
        calls.add(index=0, call_id="call-1", name="GetState", arguments="{")
        calls.add(
            index=0,
            call_id="call-1",
            name="GetState",
            arguments='{"name":"Office"}',
        )

        self.assertEqual(
            calls.complete()[0].arguments,
            {"name": "Office"},
        )

    def test_parallel_calls_are_returned_in_index_order(self) -> None:
        """Parallel calls retain the ordering supplied by Mistral."""
        calls = ToolCallAccumulator()
        calls.add(index=1, call_id="two", name="Second", arguments={})
        calls.add(index=0, call_id="one", name="First", arguments={})

        completed = calls.complete()

        self.assertEqual([call.id for call in completed], ["one", "two"])

    def test_missing_id_is_generated(self) -> None:
        """A missing provider call ID gets a stable generated ID."""
        calls = ToolCallAccumulator()
        calls.add(index=0, call_id=None, name="GetState", arguments={})

        completed = calls.complete(id_factory=lambda: "generated")

        self.assertEqual(completed[0].id, "generated")

    def test_non_object_arguments_are_rejected(self) -> None:
        """Home Assistant tools require a JSON object."""
        calls = ToolCallAccumulator()
        calls.add(index=0, call_id="call-1", name="Bad", arguments="[]")

        with self.assertRaises(ToolCallDecodeError):
            calls.complete()


if __name__ == "__main__":
    unittest.main()
