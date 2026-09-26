"""AI Task platform for Mistral AI."""

from __future__ import annotations

import asyncio
from json import JSONDecodeError
from pathlib import Path
from typing import override

import httpx
from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads
from mistralai.client.errors import MistralError, NoResponseError

from .const import (
    CONF_IMAGE_MODEL,
    DEFAULT_IMAGE_MODEL,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_AI_TASK,
)
from .coordinator import MistralConfigEntry
from .entity import MistralBaseEntity, async_prepare_image_attachments
from .image import ImageGenerationError, async_generate_image

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MistralConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Mistral AI Task entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_AI_TASK:
            continue
        async_add_entities(
            [MistralAITaskEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class MistralAITaskEntity(ai_task.AITaskEntity, MistralBaseEntity):
    """Generate Home Assistant AI Task data and images with Mistral."""

    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        | ai_task.AITaskEntityFeature.GENERATE_IMAGE
    )
    _attr_translation_key = "ai_task_data"

    def __init__(self, entry: MistralConfigEntry, subentry: ConfigSubentry) -> None:
        """Keep image requests independent from data generation settings."""
        super().__init__(entry, subentry)
        configured_model = subentry.data.get(CONF_IMAGE_MODEL, DEFAULT_IMAGE_MODEL)
        self._image_model = (
            configured_model
            if isinstance(configured_model, str) and configured_model
            else DEFAULT_IMAGE_MODEL
        )
        self._image_tasks: set[asyncio.Task[ai_task.GenImageTaskResult]] = set()
        self._image_unloading = False

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Finish cancelling requests before the shared SDK client closes."""
        self._image_unloading = True
        tasks = tuple(self._image_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await super().async_will_remove_from_hass()

    @override
    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Supervise an image request throughout the entity lifecycle."""
        if self._image_unloading:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="image_generation_error",
            )
        request = asyncio.create_task(self._async_run_image_task(task, chat_log))
        self._image_tasks.add(request)
        try:
            return await request
        finally:
            self._image_tasks.discard(request)

    async def _async_run_image_task(
        self,
        task: ai_task.GenImageTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Validate reference images and return HA's native image result."""
        attachments: list[tuple[Path, str | None]] = [
            (attachment.path, attachment.mime_type)
            for attachment in task.attachments or []
        ]
        model, known = self.coordinator.get_model_info(self._image_model)
        if attachments and known and not model.vision:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="model_no_vision",
                translation_placeholders={"model": self._image_model},
            )
        try:
            image_urls = await async_prepare_image_attachments(self.hass, attachments)
            result = await async_generate_image(
                self.hass,
                self.coordinator.client,
                self._image_model,
                task.instructions,
                image_urls,
            )
        except ImageGenerationError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=err.translation_key,
            ) from None
        except (MistralError, NoResponseError, httpx.HTTPError, TimeoutError) as err:
            translation_key, _ = await self._async_process_api_error(err)
            # SDK errors can echo the prompt or a generated file identifier.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders={"message": "Image generation request failed"},
            ) from None
        except OSError:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="image_generation_error",
            ) from None

        self.coordinator.async_set_updated_data(self.coordinator.data or [])
        return ai_task.GenImageTaskResult(
            conversation_id=chat_log.conversation_id,
            image_data=result.data,
            mime_type=result.mime_type,
        )

    @override
    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Generate unstructured or schema-constrained task data."""
        await self._async_handle_chat_log(
            chat_log,
            structure_name=task.name,
            structure=task.structure,
        )

        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="response_not_found",
            )

        text = chat_log.content[-1].content or ""
        if task.structure is None:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )

        try:
            data = json_loads(text)
        except JSONDecodeError as err:
            LOGGER.warning("Mistral returned invalid JSON for an AI Task")
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="json_parse_error",
            ) from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
