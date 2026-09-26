"""Generated-image SDK boundary and bounded download tests."""

from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from homeassistant.core import HomeAssistant
from mistralai.client import models
from PIL import Image

from custom_components.mistral_conversation import image as image_module
from custom_components.mistral_conversation.image import (
    ImageGenerationError,
    async_generate_image,
)


def _image(format_name: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format=format_name)
    return output.getvalue()


def _png() -> bytes:
    return _image()


def _client(*chunks: models.MessageOutputContentChunks) -> MagicMock:
    client = MagicMock()
    client.beta.conversations.start_async = AsyncMock(
        return_value=SimpleNamespace(
            outputs=[models.MessageOutputEntry(content=list(chunks))]
        )
    )
    client.files.delete_async = AsyncMock(
        side_effect=lambda *, file_id, **_kwargs: SimpleNamespace(
            id=file_id, deleted=True
        )
    )
    client.files.download_async = AsyncMock(
        return_value=httpx.Response(200, stream=httpx.ByteStream(_png()))
    )
    return client


def _image_chunk(file_id: str, tool: str = "image_generation") -> models.ToolFileChunk:
    return models.ToolFileChunk(tool=tool, file_id=file_id)


@pytest.mark.asyncio
async def test_generate_image_uses_one_typed_tool_request_and_deletes_all_files(
    hass: HomeAssistant,
) -> None:
    """The first generated image is returned; all unique output IDs are deleted."""
    client = _client(
        _image_chunk("file-1"),
        _image_chunk("file-1"),
        _image_chunk("other-tool", "code_interpreter"),
        _image_chunk("file-2"),
    )

    generated = await async_generate_image(
        hass,
        client,
        "mistral-medium-latest",
        "Draw a red square",
        ["data:image/png;base64,AA=="],
    )

    assert generated.data == _png()
    assert generated.mime_type == "image/png"
    request = client.beta.conversations.start_async.call_args.kwargs
    assert request["model"] == "mistral-medium-latest"
    assert request["store"] is False
    assert request["retries"] is None
    assert request["completion_args"].max_tokens == 2048
    assert len(request["tools"]) == 1
    assert isinstance(request["tools"][0], models.ImageGenerationTool)
    assert isinstance(request["inputs"][0], models.MessageInputEntry)
    assert isinstance(request["inputs"][0].content[0], models.TextChunk)
    assert isinstance(request["inputs"][0].content[1], models.ImageURLChunk)
    client.files.download_async.assert_awaited_once()
    assert client.files.download_async.call_args.kwargs["file_id"] == "file-1"
    assert client.files.download_async.call_args.kwargs["http_headers"] == {
        "Accept-Encoding": "identity"
    }
    assert [
        call.kwargs["file_id"] for call in client.files.delete_async.await_args_list
    ] == [
        "file-1",
        "file-2",
    ]


@pytest.mark.asyncio
async def test_image_generation_without_image_is_not_retried(
    hass: HomeAssistant,
) -> None:
    """A text-only response is one failed generation, not another paid request."""
    client = _client(models.TextChunk(text="I cannot produce an image"))

    with pytest.raises(ImageGenerationError, match="image_response_not_found"):
        await async_generate_image(hass, client, "model", "Draw", [])

    client.beta.conversations.start_async.assert_awaited_once()
    client.files.download_async.assert_not_awaited()
    client.files.delete_async.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("format_name", "expected_mime"),
    [
        ("GIF", "image/gif"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
    ],
)
async def test_verified_supported_image_mime(
    hass: HomeAssistant, format_name: str, expected_mime: str
) -> None:
    """The MIME follows decoded image content instead of HTTP metadata."""
    client = _client(_image_chunk("file-1"))
    client.files.download_async.return_value = httpx.Response(
        200,
        headers={"content-type": "image/png"},
        stream=httpx.ByteStream(_image(format_name)),
    )

    result = await async_generate_image(hass, client, "model", "Draw", [])

    assert result.mime_type == expected_mime


@pytest.mark.asyncio
async def test_image_at_exact_byte_limit_is_accepted(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The byte ceiling is inclusive for a valid image."""
    payload = _png()
    monkeypatch.setattr(image_module, "MAX_GENERATED_IMAGE_BYTES", len(payload))
    client = _client(_image_chunk("file-1"))

    result = await async_generate_image(hass, client, "model", "Draw", [])

    assert result.data == payload


@pytest.mark.asyncio
async def test_decompression_bomb_is_rejected_and_deleted(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tiny encoded file cannot demand unbounded image decoding."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    client = _client(_image_chunk("file-1"))

    with pytest.raises(ImageGenerationError, match="image_response_invalid"):
        await async_generate_image(hass, client, "model", "Draw", [])

    client.files.delete_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_identity_transfer_encoding_is_rejected_and_closed(
    hass: HomeAssistant,
) -> None:
    """Raw stream reads cannot silently pass compressed transfer bytes."""
    client = _client(_image_chunk("file-1"))
    response = httpx.Response(
        200,
        headers={"content-encoding": "gzip"},
        stream=httpx.ByteStream(_png()),
    )
    client.files.download_async.return_value = response

    with pytest.raises(ImageGenerationError, match="image_response_invalid"):
        await async_generate_image(hass, client, "model", "Draw", [])

    assert response.is_closed
    client.files.delete_async.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "headers", "translation_key"),
    [
        (b"", {}, "image_response_invalid"),
        (b"not an image", {}, "image_response_invalid"),
        (b"%PDF-1.7\n", {}, "image_response_invalid"),
        (_png()[:20], {}, "image_response_invalid"),
        (
            b"a",
            {"content-length": str(20 * 1024 * 1024 + 1)},
            "image_response_too_large",
        ),
        (b"x" * (20 * 1024 * 1024 + 1), {}, "image_response_too_large"),
    ],
)
async def test_invalid_or_oversized_download_is_rejected_and_deleted(
    hass: HomeAssistant, payload: bytes, headers: dict[str, str], translation_key: str
) -> None:
    """Untrusted output cannot bypass the byte and format checks."""
    client = _client(_image_chunk("file-1"))
    response = httpx.Response(200, headers=headers, stream=httpx.ByteStream(payload))
    client.files.download_async.return_value = response

    with pytest.raises(ImageGenerationError) as caught:
        await async_generate_image(hass, client, "model", "Draw", [])

    assert caught.value.translation_key == translation_key
    assert response.is_closed
    client.files.delete_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_failure_still_deletes_all_generated_files(
    hass: HomeAssistant,
) -> None:
    """A failed download does not leave known extra provider files behind."""
    client = _client(_image_chunk("file-1"), _image_chunk("file-2"))
    client.files.download_async.side_effect = httpx.ConnectError("offline")

    with pytest.raises(httpx.ConnectError):
        await async_generate_image(hass, client, "model", "Draw", [])

    assert client.files.delete_async.await_count == 2


@pytest.mark.asyncio
async def test_failed_delete_keeps_valid_image_and_logs_without_identifiers(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Best-effort cleanup cannot replace a valid result or disclose its IDs."""
    client = _client(_image_chunk("private-file-id"))
    client.files.delete_async.side_effect = None
    client.files.delete_async.return_value = SimpleNamespace(
        id="different-file", deleted=True
    )

    result = await async_generate_image(hass, client, "model", "Draw", [])

    assert result.mime_type == "image/png"
    assert "deletion was not confirmed" in caplog.text
    assert "private-file-id" not in caplog.text


@pytest.mark.asyncio
async def test_cleanup_timeout_keeps_valid_image(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider deletion has a separate bounded deadline."""
    client = _client(_image_chunk("file-1"), _image_chunk("file-2"))

    async def slow_delete(*, file_id: str, **_kwargs: object) -> SimpleNamespace:
        if file_id == "file-1":
            await asyncio.sleep(3600)
        return SimpleNamespace(id=file_id, deleted=True)

    client.files.delete_async.side_effect = slow_delete
    monkeypatch.setattr(image_module, "IMAGE_CLEANUP_TIMEOUT", 0.01)

    result = await async_generate_image(hass, client, "model", "Draw", [])

    assert result.data == _png()
    assert client.files.delete_async.await_count == 2


@pytest.mark.asyncio
async def test_generation_deadline_is_not_extended_by_provider(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation and download share the overall request deadline."""
    client = _client(_image_chunk("file-1"))

    async def slow_generation(**_kwargs: object) -> None:
        await asyncio.sleep(3600)

    client.beta.conversations.start_async.side_effect = slow_generation
    monkeypatch.setattr(image_module, "REQUEST_TIMEOUT_MS", 10)

    with pytest.raises(TimeoutError):
        await async_generate_image(hass, client, "model", "Draw", [])

    client.files.delete_async.assert_not_awaited()


class _WaitingStream(httpx.AsyncByteStream):
    def __init__(self, entered: asyncio.Event) -> None:
        self.entered = entered
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        self.entered.set()
        await asyncio.sleep(3600)
        yield b""

    async def aclose(self) -> None:
        self.closed = True


class _WaitingCloseStream(_WaitingStream):
    def __init__(
        self,
        entered: asyncio.Event,
        closing: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        super().__init__(entered)
        self.closing = closing
        self.release = release

    async def aclose(self) -> None:
        self.closing.set()
        await self.release.wait()
        await super().aclose()


class _SlowCloseImageStream(httpx.AsyncByteStream):
    def __init__(self, closing: asyncio.Event) -> None:
        self.closing = closing

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield _png()

    async def aclose(self) -> None:
        self.closing.set()
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_download_timeout_closes_response_and_deletes_file(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared deadline also applies after generation returns file IDs."""
    entered = asyncio.Event()
    stream = _WaitingStream(entered)
    client = _client(_image_chunk("file-1"))
    client.files.download_async.return_value = httpx.Response(200, stream=stream)
    monkeypatch.setattr(image_module, "REQUEST_TIMEOUT_MS", 10)

    with pytest.raises(TimeoutError):
        await async_generate_image(hass, client, "model", "Draw", [])

    assert stream.closed
    client.files.delete_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_unload_cancellation_while_closing_response_still_closes_it(
    hass: HomeAssistant,
) -> None:
    """A second cancellation cannot interrupt HTTP response closure."""
    entered = asyncio.Event()
    closing = asyncio.Event()
    release = asyncio.Event()
    stream = _WaitingCloseStream(entered, closing, release)
    client = _client(_image_chunk("file-1"))
    client.files.download_async.return_value = httpx.Response(200, stream=stream)
    request = asyncio.create_task(
        async_generate_image(hass, client, "model", "Draw", [])
    )
    await asyncio.wait_for(entered.wait(), 2)
    request.cancel()
    await asyncio.wait_for(closing.wait(), 2)
    request.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(request, 2)
    assert stream.closed
    client.files.delete_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_response_close_and_file_delete_share_cleanup_deadline(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slow HTTP closure cannot consume a second, independent deletion budget."""
    closing = asyncio.Event()
    client = _client(_image_chunk("file-1"), _image_chunk("file-2"))
    client.files.download_async.return_value = httpx.Response(
        200, stream=_SlowCloseImageStream(closing)
    )

    async def slow_delete(**_kwargs: object) -> None:
        await asyncio.sleep(3600)

    client.files.delete_async.side_effect = slow_delete
    monkeypatch.setattr(image_module, "IMAGE_CLEANUP_TIMEOUT", 0.01)
    started = asyncio.get_running_loop().time()

    result = await async_generate_image(hass, client, "model", "Draw", [])

    assert result.mime_type == "image/png"
    assert closing.is_set()
    assert client.files.delete_async.await_count == 2
    assert asyncio.get_running_loop().time() - started < 1


@pytest.mark.asyncio
async def test_repeated_cancellation_closes_download_and_completes_cleanup(
    hass: HomeAssistant,
) -> None:
    """Request cancellation and unload cancellation leave no orphaned cleanup."""
    entered = asyncio.Event()
    deleting = asyncio.Event()
    release = asyncio.Event()
    stream = _WaitingStream(entered)
    client = _client(_image_chunk("file-1"))
    client.files.download_async.return_value = httpx.Response(200, stream=stream)

    async def delete(**_kwargs: object) -> SimpleNamespace:
        deleting.set()
        await release.wait()
        return SimpleNamespace(id="file-1", deleted=True)

    client.files.delete_async.side_effect = delete
    request = asyncio.create_task(
        async_generate_image(hass, client, "model", "Draw", [])
    )
    await asyncio.wait_for(entered.wait(), 2)
    request.cancel()
    await asyncio.wait_for(deleting.wait(), 2)
    request.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(request, 2)
    assert stream.closed
    client.files.delete_async.assert_awaited_once()
