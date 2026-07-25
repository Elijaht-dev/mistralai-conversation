"""Config flow for Mistral AI Conversation."""

from __future__ import annotations

import logging
from typing import Any, cast, override

import httpx
import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LLM_HASS_API,
    CONF_MODEL,
    CONF_NAME,
    CONF_PROMPT,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
)
from mistralai.client.errors import MistralError, NoResponseError

from .api import async_validate_api_key
from .const import (
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SAFE_PROMPT,
    CONF_TEMPERATURE,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_CONVERSATION_OPTIONS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TITLE,
    DOMAIN,
    MAX_CONFIGURED_TOKENS,
    REASONING_EFFORT_NONE,
    REASONING_EFFORTS,
    ApiErrorKind,
    MistralModel,
)
from .coordinator import MistralConfigEntry
from .errors import classify_api_error

_LOGGER = logging.getLogger(__name__)


def _connection_error_key(err: BaseException) -> str:
    """Map a provider exception to a config-flow error key."""
    error_kind = classify_api_error(err)
    if error_kind is ApiErrorKind.AUTHENTICATION:
        return "invalid_auth"
    if error_kind is ApiErrorKind.TIMEOUT:
        return "timeout_connect"
    return "cannot_connect"


def _validate_model_options(
    model: MistralModel,
    known: bool,
    options: dict[str, Any],
) -> dict[str, str]:
    """Validate options against advertised model capabilities."""
    if not known:
        return {}

    errors: dict[str, str] = {}
    if options.get(CONF_LLM_HASS_API) and not model.function_calling:
        errors[CONF_MODEL] = "model_no_tools"

    if (
        options.get(CONF_REASONING_EFFORT, REASONING_EFFORT_NONE)
        != REASONING_EFFORT_NONE
        and not model.reasoning
    ):
        errors[CONF_REASONING_EFFORT] = "model_no_reasoning"

    max_tokens = options.get(CONF_MAX_TOKENS)
    if (
        isinstance(max_tokens, int)
        and model.max_context_length is not None
        and max_tokens > model.max_context_length
    ):
        errors[CONF_MAX_TOKENS] = "max_tokens_context"

    return errors


class MistralConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mistral AI Conversation."""

    VERSION = 1
    MINOR_VERSION = 2

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the supported config subentry types."""
        return {"conversation": ConversationSubentryFlowHandler}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            self._async_abort_entries_match({CONF_API_KEY: api_key})
            try:
                await async_validate_api_key(self.hass, api_key)
            except (
                MistralError,
                NoResponseError,
                httpx.HTTPError,
                TimeoutError,
            ) as err:
                errors["base"] = _connection_error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected exception validating Mistral API key")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=DEFAULT_TITLE,
                    data={CONF_API_KEY: api_key},
                    subentries=[
                        {
                            "subentry_type": "conversation",
                            "data": DEFAULT_CONVERSATION_OPTIONS,
                            "title": DEFAULT_CONVERSATION_NAME,
                            "unique_id": None,
                        }
                    ],
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors or None,
            description_placeholders={
                "api_key_url": "https://console.mistral.ai/api-keys/"
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save a replacement API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            try:
                await async_validate_api_key(self.hass, api_key)
            except (
                MistralError,
                NoResponseError,
                httpx.HTTPError,
                TimeoutError,
            ) as err:
                errors["base"] = _connection_error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected exception reauthenticating Mistral")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors or None,
        )


class ConversationSubentryFlowHandler(ConfigSubentryFlow):
    """Handle conversation-agent subentries."""

    options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        """Return whether a new subentry is being created."""
        return self.source == SOURCE_USER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Create a conversation agent."""
        self.options = DEFAULT_CONVERSATION_OPTIONS.copy()
        return await self.async_step_init(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure a conversation agent."""
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init(user_input)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure a conversation agent."""
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        config_entry = cast(MistralConfigEntry, entry)
        coordinator = config_entry.runtime_data
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        suggested = self.options

        if user_input is not None:
            suggested = self.options | user_input
            model_id = user_input[CONF_MODEL]
            model, known = coordinator.get_model_info(model_id)
            errors = _validate_model_options(model, known, user_input)
            if model.max_context_length is not None:
                description_placeholders["max_context_length"] = str(
                    model.max_context_length
                )

            if not errors:
                data = user_input.copy()
                name = data.pop(CONF_NAME)
                if not data.get(CONF_LLM_HASS_API):
                    data.pop(CONF_LLM_HASS_API, None)

                if self._is_new:
                    return self.async_create_entry(title=name, data=data)

                return self.async_update_and_abort(
                    entry,
                    self._get_reconfigure_subentry(),
                    title=name,
                    data=data,
                )

        configured_model = suggested.get(CONF_MODEL, DEFAULT_MODEL)
        model_options = [
            SelectOptionDict(value=model.id, label=model.label)
            for model in coordinator.data or []
        ]
        known_model_ids = {model["value"] for model in model_options}
        if configured_model not in known_model_ids:
            model_options.append(
                SelectOptionDict(value=configured_model, label=configured_model)
            )

        hass_apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        known_api_ids = {api["value"] for api in hass_apis}
        selected_apis = suggested.get(CONF_LLM_HASS_API, [])
        if isinstance(selected_apis, str):
            selected_apis = [selected_apis]
        selected_apis = [api_id for api_id in selected_apis if api_id in known_api_ids]

        subentry_name = (
            suggested.get(CONF_NAME, DEFAULT_CONVERSATION_NAME)
            if self._is_new
            else suggested.get(CONF_NAME, self._get_reconfigure_subentry().title)
        )

        selected_model, selected_model_known = coordinator.get_model_info(
            configured_model
        )
        max_token_limit = MAX_CONFIGURED_TOKENS
        if selected_model_known and selected_model.max_context_length:
            max_token_limit = min(
                MAX_CONFIGURED_TOKENS, selected_model.max_context_length
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=subentry_name): cv.string,
                vol.Required(
                    CONF_MODEL,
                    default=configured_model,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=model_options,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=True,
                    )
                ),
                vol.Optional(CONF_PROMPT): TemplateSelector(),
                vol.Optional(
                    CONF_LLM_HASS_API,
                    default=selected_apis,
                ): SelectSelector(
                    SelectSelectorConfig(options=hass_apis, multiple=True)
                ),
                vol.Required(
                    CONF_MAX_TOKENS,
                    default=suggested.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS),
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=max_token_limit,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Required(
                    CONF_TEMPERATURE,
                    default=suggested.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=1,
                        step=0.05,
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(
                    CONF_REASONING_EFFORT,
                    default=suggested.get(CONF_REASONING_EFFORT, REASONING_EFFORT_NONE),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(REASONING_EFFORTS),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_REASONING_EFFORT,
                    )
                ),
                vol.Required(
                    CONF_SAFE_PROMPT,
                    default=suggested.get(CONF_SAFE_PROMPT, False),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors or None,
            description_placeholders=description_placeholders or None,
            last_step=True,
        )
