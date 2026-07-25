"""Diagnostics support for Mistral AI Conversation."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .coordinator import MistralConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_PROMPT}


def _sdk_version() -> str:
    """Return the installed Mistral SDK version."""
    try:
        return version("mistralai")
    except PackageNotFoundError:
        return "unknown"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MistralConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "client": f"mistralai=={_sdk_version()}",
        "title": entry.title,
        "entry_id": entry.entry_id,
        "entry_version": f"{entry.version}.{entry.minor_version}",
        "state": entry.state.value,
        "data": async_redact_data(entry.data, TO_REDACT),
        "options": async_redact_data(entry.options, TO_REDACT),
        "subentries": {
            subentry.subentry_id: {
                "title": subentry.title,
                "subentry_type": subentry.subentry_type,
                "data": async_redact_data(subentry.data, TO_REDACT),
            }
            for subentry in entry.subentries.values()
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "models": [
                {
                    "id": model.id,
                    "name": model.name,
                    "aliases": model.aliases,
                    "function_calling": model.function_calling,
                    "reasoning": model.reasoning,
                    "vision": model.vision,
                    "ocr": model.ocr,
                    "max_context_length": model.max_context_length,
                    "default_temperature": model.default_temperature,
                    "deprecation": (
                        model.deprecation.isoformat()
                        if model.deprecation is not None
                        else None
                    ),
                    "replacement_model": model.replacement_model,
                }
                for model in coordinator.data or []
            ],
        },
        "entities": {
            entity_entry.entity_id: entity_entry.extended_dict
            for entity_entry in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
        },
    }
