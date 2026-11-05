"""Import HA helpers from the fixture package matching the test environment."""

from contextlib import contextmanager
from importlib.util import find_spec
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers import llm

if find_spec("homeassistant.components.llm") is not None:
    from homeassistant.components.llm import LLMTools

if find_spec("pytest_homeassistant") is not None:
    from pytest_homeassistant.common import MockConfigEntry
else:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

if find_spec("probatio") is not None:
    from probatio import to_field_list
else:
    from voluptuous_serialize import convert as to_field_list


@contextmanager
def mock_llm_tools(tools):
    """Mock the native tool-discovery surface for the installed HA version."""
    if find_spec("homeassistant.components.llm") is not None:
        with patch(
            "homeassistant.components.llm.async_get_tools",
            new_callable=AsyncMock,
            return_value=LLMTools(tools=tools),
        ):
            yield
    else:

        async def get_api(hass, api_ids, llm_context):
            return llm.APIInstance(
                api=MagicMock(), api_prompt="", llm_context=llm_context, tools=tools
            )

        with patch("homeassistant.helpers.llm.async_get_api", side_effect=get_api):
            yield


__all__ = ["MockConfigEntry", "mock_llm_tools", "to_field_list"]
