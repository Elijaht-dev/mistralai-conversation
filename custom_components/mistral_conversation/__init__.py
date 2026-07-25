"""The Mistral AI Conversation integration."""

from __future__ import annotations

from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LLM_HASS_API, CONF_MODEL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SAFE_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_CONVERSATION_OPTIONS,
    DOMAIN,
    LOGGER,
    REASONING_EFFORTS,
)
from .coordinator import MistralConfigEntry, MistralCoordinator

PLATFORMS = (Platform.CONVERSATION,)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_DEPRECATION_ISSUE_PREFIX = "model_deprecated_"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Mistral AI Conversation."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MistralConfigEntry) -> bool:
    """Set up Mistral AI Conversation from a config entry."""
    coordinator = MistralCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await coordinator.async_close()
        raise

    entry.runtime_data = coordinator
    LOGGER.debug(
        "Available Mistral chat models: %s",
        [model.id for model in coordinator.data or []],
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_entry))
    entry.async_on_unload(
        coordinator.async_add_listener(
            partial(_async_update_deprecation_issues, hass, entry)
        )
    )
    _async_update_deprecation_issues(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MistralConfigEntry) -> bool:
    """Unload a Mistral AI Conversation config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.async_close()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: MistralConfigEntry) -> None:
    """Remove issues owned by a deleted config entry."""
    for subentry in entry.subentries.values():
        ir.async_delete_issue(
            hass,
            DOMAIN,
            f"{_DEPRECATION_ISSUE_PREFIX}{subentry.subentry_id}",
        )


async def _async_update_entry(hass: HomeAssistant, entry: MistralConfigEntry) -> None:
    """Reload the entry when its configuration changes."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_update_deprecation_issues(
    hass: HomeAssistant, entry: MistralConfigEntry
) -> None:
    """Create or clear repair issues for deprecated configured models."""
    coordinator = entry.runtime_data

    for subentry in entry.subentries.values():
        issue_id = f"{_DEPRECATION_ISSUE_PREFIX}{subentry.subentry_id}"
        configured_model = subentry.data.get(CONF_MODEL)
        if not isinstance(configured_model, str):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue

        model, known = coordinator.get_model_info(configured_model)
        if not known or model.deprecation is None:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue

        replacement = model.replacement_model or ""
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=bool(replacement),
            is_persistent=False,
            learn_more_url="https://docs.mistral.ai/getting-started/models/models_overview/",
            severity=ir.IssueSeverity.WARNING,
            translation_key="model_deprecated",
            translation_placeholders={
                "model": configured_model,
                "replacement": replacement or "another supported model",
                "retirement_date": model.deprecation.date().isoformat(),
                "subentry_name": subentry.title,
            },
            data={
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "replacement": replacement,
            },
        )


def _migrate_subentry_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize data stored by the initial private preview."""
    migrated = data.copy()
    for key in (
        CONF_MAX_TOKENS,
        CONF_REASONING_EFFORT,
        CONF_SAFE_PROMPT,
        CONF_TEMPERATURE,
        CONF_MODEL,
    ):
        migrated.setdefault(key, DEFAULT_CONVERSATION_OPTIONS[key])

    if isinstance((api_ids := migrated.get(CONF_LLM_HASS_API)), str):
        migrated[CONF_LLM_HASS_API] = [api_ids]

    reasoning_effort = migrated.get(CONF_REASONING_EFFORT)
    if reasoning_effort not in REASONING_EFFORTS:
        migrated[CONF_REASONING_EFFORT] = DEFAULT_CONVERSATION_OPTIONS[
            CONF_REASONING_EFFORT
        ]

    max_tokens = migrated.get(CONF_MAX_TOKENS)
    if type(max_tokens) is not int or max_tokens < 1:
        migrated[CONF_MAX_TOKENS] = DEFAULT_CONVERSATION_OPTIONS[CONF_MAX_TOKENS]

    temperature = migrated.get(CONF_TEMPERATURE)
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, int | float)
        or not 0 <= temperature <= 1
    ):
        migrated[CONF_TEMPERATURE] = DEFAULT_CONVERSATION_OPTIONS[CONF_TEMPERATURE]

    if not isinstance(migrated.get(CONF_SAFE_PROMPT), bool):
        migrated[CONF_SAFE_PROMPT] = DEFAULT_CONVERSATION_OPTIONS[CONF_SAFE_PROMPT]

    return migrated


async def async_migrate_entry(
    hass: HomeAssistant, entry: ConfigEntry[MistralCoordinator]
) -> bool:
    """Migrate config entries created by earlier private previews."""
    LOGGER.debug(
        "Migrating Mistral entry from version %s.%s",
        entry.version,
        entry.minor_version,
    )

    if entry.version != 1:
        LOGGER.error(
            "Cannot migrate unsupported Mistral entry version %s", entry.version
        )
        return False

    if entry.minor_version < 2:
        for subentry in entry.subentries.values():
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data=_migrate_subentry_data(dict(subentry.data)),
            )
        hass.config_entries.async_update_entry(entry, minor_version=2)

    return True


__all__ = ["MistralConfigEntry", "MistralCoordinator"]
