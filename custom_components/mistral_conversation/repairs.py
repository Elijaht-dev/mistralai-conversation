"""Repair flows for Mistral AI Conversation."""

from __future__ import annotations

from typing import cast

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import DOMAIN
from .coordinator import MistralConfigEntry

_DEPRECATION_ISSUE_PREFIX = "model_deprecated_"


class DeprecatedModelRepairFlow(RepairsFlow):
    """Replace a deprecated model on one configured entity subentry."""

    def __init__(
        self,
        entry_id: str,
        subentry_id: str,
        replacement: str,
    ) -> None:
        """Initialize the repair flow."""
        super().__init__()
        self._entry_id = entry_id
        self._subentry_id = subentry_id
        self._replacement = replacement

    def _target(self) -> tuple[MistralConfigEntry, ConfigSubentry]:
        """Resolve the target entry and subentry."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if (
            entry is None
            or (subentry := entry.subentries.get(self._subentry_id)) is None
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="subentry_not_found",
            )
        return cast(MistralConfigEntry, entry), subentry

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Select and save a supported replacement model."""
        entry, subentry = self._target()

        if user_input is not None:
            self.hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={**subentry.data, CONF_MODEL: user_input[CONF_MODEL]},
            )
            return self.async_create_entry(data={})

        options: list[SelectOptionDict] = []
        if entry.state is ConfigEntryState.LOADED:
            options = [
                SelectOptionDict(value=model.id, label=model.label)
                for model in entry.runtime_data.data or []
                if model.deprecation is None
            ]

        known_ids = {option["value"] for option in options}
        if self._replacement not in known_ids:
            options.append(
                SelectOptionDict(
                    value=self._replacement,
                    label=self._replacement,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL,
                        default=self._replacement,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            custom_value=True,
                        )
                    )
                }
            ),
            description_placeholders={
                "model": str(subentry.data.get(CONF_MODEL, "")),
                "subentry_name": subentry.title,
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a repair flow for an integration issue."""
    if not issue_id.startswith(_DEPRECATION_ISSUE_PREFIX) or data is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unknown_issue_id",
        )

    entry_id = data.get("entry_id")
    subentry_id = data.get("subentry_id")
    replacement = data.get("replacement")
    if not all(
        isinstance(value, str) and value
        for value in (entry_id, subentry_id, replacement)
    ):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invalid_repair_data",
        )

    return DeprecatedModelRepairFlow(
        cast(str, entry_id),
        cast(str, subentry_id),
        cast(str, replacement),
    )
