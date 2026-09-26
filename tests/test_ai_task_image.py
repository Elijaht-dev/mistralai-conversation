"""Public Home Assistant AI Task image-generation behavior."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from homeassistant.components import ai_task, conversation
from homeassistant.const import CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mistral_conversation.const import (
    CONF_IMAGE_MODEL,
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_SAFE_PROMPT,
    CONF_TEMPERATURE,
    SUBENTRY_TYPE_AI_TASK,
    MistralModel,
)
from custom_components.mistral_conversation.image import (
    GeneratedImage,
    ImageGenerationError,
)

from .helpers import mistral_error

ENTITY_ID = "ai_task.mistral_ai_task"


@pytest.fixture
def image_bytes() -> bytes:
    """A real, small PNG for Home Assistant's media storage."""
    output = BytesIO()
    Image.new("RGB", (2, 3), color="blue").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def media_directory(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Give Home Assistant's image task a local media directory."""
    hass.config.media_dirs["local"] = str(tmp_path)
    return tmp_path


@pytest.fixture
def configured_image_model(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> str:
    """Persist a custom image model with separate data-generation controls."""
    subentry = next(
        entry
        for entry in mock_config_entry.subentries.values()
        if entry.subentry_type == SUBENTRY_TYPE_AI_TASK
    )
    image_model = "ft:custom-image-model"
    hass.config_entries.async_update_subentry(
        mock_config_entry,
        subentry,
        data={
            **subentry.data,
            CONF_IMAGE_MODEL: image_model,
            CONF_MODEL: "mistral-small-latest",
            CONF_MAX_TOKENS: 17,
            CONF_REASONING_EFFORT: "none",
            CONF_SAFE_PROMPT: True,
            CONF_TEMPERATURE: 0.8,
        },
    )
    return image_model


def _reference(path: Path, mime_type: str = "image/png") -> conversation.Attachment:
    return conversation.Attachment(
        media_content_id="media-source://media_source/local/reference.png",
        mime_type=mime_type,
        path=path,
    )


async def _generate(hass: HomeAssistant, *, attachments: list[dict] | None = None):
    return await ai_task.async_generate_image(
        hass,
        task_name="Test picture",
        entity_id=ENTITY_ID,
        instructions="Draw a lighthouse",
        attachments=attachments,
    )


async def test_generate_image_uses_default_model_and_ha_media_storage(
    hass: HomeAssistant,
    media_directory: Path,
    mock_init_component: MagicMock,
    image_bytes: bytes,
) -> None:
    """The public action stores bytes in HA and returns a media source URL."""
    generate = AsyncMock(
        return_value=GeneratedImage(data=image_bytes, mime_type="image/png")
    )
    with patch(
        "custom_components.mistral_conversation.ai_task.async_generate_image",
        generate,
    ):
        result = await _generate(hass)

    assert result["mime_type"] == "image/png"
    assert result["conversation_id"]
    assert result["media_source_id"].startswith("media-source://ai_task/")
    assert result["url"].startswith("/ai_task/image/")
    assert "image_data" not in result
    assert result["model"] is None
    assert result["width"] is None
    assert result["height"] is None
    assert result["revised_prompt"] == "Draw a lighthouse"
    files = await hass.async_add_executor_job(
        lambda: list(media_directory.rglob("*.png"))
    )
    assert len(files) == 1
    assert await hass.async_add_executor_job(files[0].read_bytes) == image_bytes
    assert generate.await_args.args[2:] == (
        "mistral-medium-latest",
        "Draw a lighthouse",
        [],
    )
    mock_init_component.chat.stream_async.assert_not_awaited()


async def test_generate_image_uses_only_image_references(
    hass: HomeAssistant,
    media_directory: Path,
    configured_image_model: str,
    mock_init_component: MagicMock,
    image_bytes: bytes,
    tmp_path: Path,
) -> None:
    """A reference is encoded and sent to image generation, not data chat."""
    reference = tmp_path / "reference.png"
    reference.write_bytes(image_bytes)
    generate = AsyncMock(
        return_value=GeneratedImage(data=image_bytes, mime_type="image/png")
    )
    with (
        patch(
            "homeassistant.components.ai_task.task._resolve_attachments",
            new=AsyncMock(return_value=[_reference(reference)]),
        ),
        patch(
            "custom_components.mistral_conversation.ai_task.async_generate_image",
            generate,
        ),
    ):
        result = await _generate(
            hass,
            attachments=[
                {"media_content_id": "media-source://media_source/local/reference.png"}
            ],
        )

    assert result["media_source_id"]
    image_urls = generate.await_args.args[4]
    assert generate.await_args.args[2] == configured_image_model
    assert len(image_urls) == 1
    assert image_urls[0].startswith("data:image/png;base64,")
    assert base64.b64decode(image_urls[0].split(",", 1)[1]) == image_bytes
    mock_init_component.chat.stream_async.assert_not_awaited()


@pytest.mark.parametrize(
    ("mime_type", "translation_key"),
    [
        ("application/pdf", "image_attachment_unsupported"),
        ("text/plain", "image_attachment_unsupported"),
    ],
)
async def test_image_rejects_non_image_reference_before_provider(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    tmp_path: Path,
    mime_type: str,
    translation_key: str,
) -> None:
    """Image generation accepts only the supported image attachment types."""
    reference = tmp_path / "reference.pdf"
    reference.write_bytes(b"%PDF")
    generate = AsyncMock()
    with (
        patch(
            "homeassistant.components.ai_task.task._resolve_attachments",
            new=AsyncMock(return_value=[_reference(reference, mime_type)]),
        ),
        patch(
            "custom_components.mistral_conversation.ai_task.async_generate_image",
            generate,
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await _generate(
            hass,
            attachments=[
                {"media_content_id": "media-source://media_source/local/reference.png"}
            ],
        )
    assert raised.value.translation_key == translation_key
    generate.assert_not_awaited()


@pytest.mark.parametrize(
    ("count", "max_bytes", "translation_key"),
    [
        (11, None, "too_many_attachments"),
        (1, 1, "attachment_too_large"),
    ],
)
async def test_image_reference_limits_prevent_provider_request(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    image_bytes: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    max_bytes: int | None,
    translation_key: str,
) -> None:
    """Reference count and bytes are bounded before sending the prompt."""
    reference = tmp_path / "reference.png"
    reference.write_bytes(image_bytes)
    if max_bytes is not None:
        monkeypatch.setattr(
            "custom_components.mistral_conversation.entity.MAX_ATTACHMENT_BYTES",
            max_bytes,
        )
    attachments = [_reference(reference)] * count
    generate = AsyncMock()
    with (
        patch(
            "homeassistant.components.ai_task.task._resolve_attachments",
            new=AsyncMock(return_value=attachments),
        ),
        patch(
            "custom_components.mistral_conversation.ai_task.async_generate_image",
            generate,
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await _generate(
            hass,
            attachments=[
                {"media_content_id": "media-source://media_source/local/reference.png"}
            ],
        )
    assert raised.value.translation_key == translation_key
    generate.assert_not_awaited()


async def test_image_reference_growth_after_stat_is_bounded(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    image_bytes: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file that grows after its size check cannot exceed the read limit."""
    reference = tmp_path / "reference.png"
    reference.write_bytes(image_bytes)
    monkeypatch.setattr(
        "custom_components.mistral_conversation.entity.MAX_ATTACHMENT_BYTES", 1
    )
    original_stat = Path.stat

    def stale_stat(path: Path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == reference:
            return SimpleNamespace(st_mode=result.st_mode, st_size=1)
        return result

    monkeypatch.setattr(Path, "stat", stale_stat)
    generate = AsyncMock()
    with (
        patch(
            "homeassistant.components.ai_task.task._resolve_attachments",
            new=AsyncMock(return_value=[_reference(reference)]),
        ),
        patch(
            "custom_components.mistral_conversation.ai_task.async_generate_image",
            generate,
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await _generate(
            hass,
            attachments=[
                {"media_content_id": "media-source://media_source/local/reference.png"}
            ],
        )
    assert raised.value.translation_key == "attachment_too_large"
    generate.assert_not_awaited()


async def test_known_image_model_without_vision_rejects_references(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    model: MistralModel,
    tmp_path: Path,
) -> None:
    """Known model capability metadata blocks unsupported visual input."""
    entity = hass.data[ai_task.DOMAIN].get_entity(ENTITY_ID)
    entity._image_model = model.id
    entity.coordinator.get_model_info = MagicMock(
        return_value=(replace(model, vision=False), True)
    )
    generate = AsyncMock()
    with (
        patch(
            "homeassistant.components.ai_task.task._resolve_attachments",
            new=AsyncMock(return_value=[_reference(tmp_path / "image.png")]),
        ),
        patch(
            "custom_components.mistral_conversation.ai_task.async_generate_image",
            generate,
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await _generate(
            hass,
            attachments=[
                {"media_content_id": "media-source://media_source/local/reference.png"}
            ],
        )
    assert raised.value.translation_key == "model_no_vision"
    generate.assert_not_awaited()


@pytest.mark.parametrize(
    "translation_key",
    ["image_response_not_found", "image_response_invalid", "image_response_too_large"],
)
async def test_image_result_errors_are_translated(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    translation_key: str,
) -> None:
    """Missing, corrupt and oversized provider output stay distinct to users."""
    with (
        patch(
            "custom_components.mistral_conversation.ai_task.async_generate_image",
            new=AsyncMock(side_effect=ImageGenerationError(translation_key)),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await _generate(hass)
    assert raised.value.translation_key == translation_key


@pytest.mark.parametrize(
    ("error", "translation_key", "unavailable", "refresh"),
    [
        (
            mistral_error(401, "secret prompt and file ID"),
            "api_authentication_error",
            False,
            True,
        ),
        (
            mistral_error(403, "secret prompt and file ID"),
            "api_authentication_error",
            False,
            True,
        ),
        (
            mistral_error(429, "secret prompt and file ID"),
            "api_rate_limit",
            False,
            False,
        ),
        (
            mistral_error(500, "secret prompt and file ID"),
            "api_connection_error",
            True,
            False,
        ),
        (httpx.ReadTimeout("secret prompt and file ID"), "api_timeout", True, False),
        (
            httpx.ConnectError("secret prompt and file ID"),
            "api_connection_error",
            True,
            False,
        ),
    ],
)
async def test_image_provider_error_is_classified_without_private_text(
    hass: HomeAssistant,
    mock_init_component: MagicMock,
    error: Exception,
    translation_key: str,
    unavailable: bool,
    refresh: bool,
) -> None:
    """The public error preserves health semantics without exposing private text."""
    entity = hass.data[ai_task.DOMAIN].get_entity(ENTITY_ID)
    coordinator = entity.coordinator
    assert coordinator.last_update_success
    with (
        patch.object(
            coordinator, "async_request_refresh", new_callable=AsyncMock
        ) as request_refresh,
        patch(
            "custom_components.mistral_conversation.ai_task.async_generate_image",
            new=AsyncMock(side_effect=error),
        ),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await _generate(hass)
    assert raised.value.translation_key == translation_key
    assert raised.value.translation_placeholders == {
        "message": "Image generation request failed"
    }
    assert "secret prompt" not in str(raised.value)
    assert coordinator.last_update_success is (not unavailable)
    assert entity.available is (not unavailable)
    if refresh:
        request_refresh.assert_awaited_once()
    else:
        request_refresh.assert_not_awaited()


async def test_unload_cancels_active_image_request(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_init_component: MagicMock,
) -> None:
    """Removing the entity cancels image work before closing the shared client."""
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def generate(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with patch(
        "custom_components.mistral_conversation.ai_task.async_generate_image",
        new=AsyncMock(side_effect=generate),
    ):
        request = asyncio.create_task(_generate(hass))
        await entered.wait()
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        with pytest.raises(asyncio.CancelledError):
            await request
    assert cancelled.is_set()
