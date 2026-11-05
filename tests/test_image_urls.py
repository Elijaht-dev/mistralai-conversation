"""Signed image-tool results alongside the original Files API response."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import httpx2
import pytest
from homeassistant.core import HomeAssistant
from mistralai.client import models

from custom_components.mistral_conversation import image as image_module
from custom_components.mistral_conversation.api import ImageGenerationSources
from custom_components.mistral_conversation.const import ApiErrorKind
from custom_components.mistral_conversation.errors import (
    ImageDownloadError,
    classify_api_error,
)
from custom_components.mistral_conversation.image import (
    ImageGenerationError,
    async_generate_image,
)

from .test_image import _client, _image_chunk, _png, _WaitingStream

STORAGE_URL = (
    "https://testimages.blob.core.windows.net/generated/test.png?sig=test-signature"
)


def _tool(result: object, name: str = "image_generation") -> models.ToolExecutionEntry:
    return models.ToolExecutionEntry(name=name, arguments="{}", info={"result": result})


def _url_client(result: object = None) -> MagicMock:
    client = _client()
    client.beta.conversations.start_async.return_value.outputs.insert(
        0, _tool(result if result is not None else json.dumps({"url": STORAGE_URL}))
    )
    return client


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize(
    "url", [STORAGE_URL, "https://files.mistral.ai/generated/test.png"]
)
async def test_structured_tool_url_uses_transport_without_credentials(
    hass: HomeAssistant, serialized: bool, url: str
) -> None:
    """A signed URL is read once without inheriting SDK/client credentials."""
    requests = []
    response = httpx2.Response(200, stream=httpx2.ByteStream(_png()))

    async def download(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return response

    result = {"url": url}
    client = _url_client(json.dumps(result) if serialized else result)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(download),
        auth=("test-user", "test-password"),
        headers={"Authorization": "Bearer test-api-key", "X-Private": "test-private"},
        cookies={"session": "test-cookie"},
        params={"api_key": "test-api-key"},
        follow_redirects=True,
    ) as transport:
        client.sdk_configuration.async_client = transport
        generated = await async_generate_image(hass, client, "model", "Draw", [])
        assert not transport.is_closed

    assert generated.data == _png()
    assert generated.mime_type == "image/png"
    assert response.is_closed
    assert len(requests) == 1
    assert str(requests[0].url) == url
    assert requests[0].method == "GET"
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert not {"authorization", "cookie", "x-private"}.intersection(
        requests[0].headers
    )
    client.beta.conversations.start_async.assert_awaited_once()
    client.files.download_async.assert_not_awaited()
    client.files.delete_async.assert_not_awaited()


@pytest.mark.parametrize(
    "result",
    [None, "invalid JSON", "[]", "null", {}, {"url": 12}, {"url": ""}],
)
async def test_missing_or_malformed_tool_result_does_not_use_assistant_links(
    hass: HomeAssistant, result: object
) -> None:
    """An assistant URL cannot turn a missing tool result into a download."""
    client = _url_client()
    client.beta.conversations.start_async.return_value.outputs = [
        _tool(result),
        models.MessageOutputEntry(content=f"![image]({STORAGE_URL})"),
    ]
    with pytest.raises(ImageGenerationError, match="image_response_not_found"):
        await async_generate_image(hass, client, "model", "Draw", [])
    client.sdk_configuration.async_client.send.assert_not_called()


async def test_other_tools_and_absent_info_cannot_supply_image_urls(
    hass: HomeAssistant,
) -> None:
    """Only the image-generation tool's structured result is authoritative."""
    client = _url_client()
    client.beta.conversations.start_async.return_value.outputs = [
        _tool({"url": STORAGE_URL}, "code_interpreter"),
        models.ToolExecutionEntry(name="image_generation", arguments="{}"),
    ]
    with pytest.raises(ImageGenerationError, match="image_response_not_found"):
        await async_generate_image(hass, client, "model", "Draw", [])
    client.sdk_configuration.async_client.send.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://files.mistral.ai/image.png",
        "https://example.com/image.png",
        "https://localhost/image.png",
        "https://127.0.0.1/image.png",
        "https://169.254.169.254/image.png",
        "https://[::1]/image.png",
        "https://files.mistral.ai.example.com/image.png",
        "https://testimages.blob.core.windows.net.example.com/image.png?sig=test",
        "https://nested.testimages.blob.core.windows.net/image.png?sig=test",
        "https://testimages.blob.core.windows.net/image.png",
        "https://testimages.blob.core.windows.net/image.png?sig=",
        "https://user@files.mistral.ai/image.png",
        "https://:password@files.mistral.ai/image.png",
        "https://files.mistral.ai:444/image.png",
        "https://files.mistral.ai:invalid/image.png",
        "https://files.mistral.ai/image.png#fragment",
        "https://files.mistral.ai\\@localhost/image.png",
        "https://files.mistral.ai/image.png\r\nX-Test: injected",
        "https://files.mistral.ai/image with spaces.png",
        "https://files.mistral.ai",
        "file:///tmp/image.png",
        "//files.mistral.ai/image.png",
    ],
)
async def test_unapproved_storage_urls_are_rejected_before_network_access(
    hass: HomeAssistant, url: str
) -> None:
    """Generated metadata cannot select arbitrary destinations or identities."""
    client = _url_client({"url": url})
    with pytest.raises(ImageDownloadError) as caught:
        await async_generate_image(hass, client, "model", "Draw", [])
    assert url not in str(caught.value)
    client.sdk_configuration.async_client.send.assert_not_called()


@pytest.mark.parametrize("download_fails", [False, True])
async def test_file_ids_keep_priority_and_never_retry_via_url(
    hass: HomeAssistant, download_fails: bool
) -> None:
    """Mixed responses preserve Files download, deletion and failure behavior."""
    client = _client(_image_chunk("file-1"), _image_chunk("file-1"))
    client.beta.conversations.start_async.return_value.outputs.insert(
        0, _tool({"url": STORAGE_URL})
    )
    if download_fails:
        client.files.download_async.side_effect = httpx2.ConnectError("offline")
        with pytest.raises(httpx2.ConnectError):
            await async_generate_image(hass, client, "model", "Draw", [])
    else:
        result = await async_generate_image(hass, client, "model", "Draw", [])
        assert result.data == _png()
    client.files.download_async.assert_awaited_once()
    client.files.delete_async.assert_awaited_once()
    client.sdk_configuration.async_client.send.assert_not_called()


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (302, ApiErrorKind.API),
        (307, ApiErrorKind.API),
        (401, ApiErrorKind.API),
        (403, ApiErrorKind.API),
        (404, ApiErrorKind.API),
        (408, ApiErrorKind.TIMEOUT),
        (429, ApiErrorKind.RATE_LIMIT),
        (500, ApiErrorKind.CONNECTION),
        (504, ApiErrorKind.TIMEOUT),
    ],
)
async def test_storage_errors_are_safe_and_redirects_are_not_followed(
    hass: HomeAssistant, status: int, kind: ApiErrorKind
) -> None:
    """Storage auth failures do not request Mistral-key reauthentication."""
    requests = []
    response = httpx2.Response(
        status,
        headers={"Location": "https://localhost/private"},
        stream=httpx2.ByteStream(b"private storage error details"),
    )

    def download(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return response

    client = _url_client()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(download), follow_redirects=True
    ) as transport:
        client.sdk_configuration.async_client = transport
        with pytest.raises(ImageDownloadError) as caught:
            await async_generate_image(hass, client, "model", "Draw", [])
    assert classify_api_error(caught.value) is kind
    assert "test-signature" not in str(caught.value)
    assert "private storage" not in str(caught.value)
    assert len(requests) == 1
    assert response.is_closed


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (httpx2.ConnectError(STORAGE_URL), ApiErrorKind.CONNECTION),
        (httpx2.ReadTimeout(STORAGE_URL), ApiErrorKind.TIMEOUT),
    ],
)
async def test_transport_failures_do_not_expose_signed_url(
    hass: HomeAssistant, error: Exception, kind: ApiErrorKind
) -> None:
    """The cause of a connection failure may contain a secret signed URL."""

    def download(request: httpx2.Request) -> httpx2.Response:
        raise error

    client = _url_client()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(download)
    ) as transport:
        client.sdk_configuration.async_client = transport
        with pytest.raises(ImageDownloadError) as caught:
            await async_generate_image(hass, client, "model", "Draw", [])
    assert classify_api_error(caught.value) is kind
    assert "test-signature" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("payload", [b"", b"not an image", _png()[:20], b"x" * 100])
async def test_url_download_keeps_image_and_size_validation(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    """URL images cannot bypass the existing byte and image-format bounds."""
    monkeypatch.setattr(image_module, "MAX_GENERATED_IMAGE_BYTES", len(_png()))
    response = httpx2.Response(200, stream=httpx2.ByteStream(payload))
    client = _url_client()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: response)
    ) as transport:
        client.sdk_configuration.async_client = transport
        with pytest.raises(ImageGenerationError):
            await async_generate_image(hass, client, "model", "Draw", [])
    assert response.is_closed


@pytest.mark.parametrize("cancel", [False, True])
async def test_url_download_timeout_and_cancellation_close_response(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    """URL responses share the deadline and cancellation-safe finalizer."""
    entered = asyncio.Event()
    stream = _WaitingStream(entered)
    response = httpx2.Response(200, stream=stream)
    client = _url_client()
    if not cancel:
        monkeypatch.setattr(image_module, "REQUEST_TIMEOUT_MS", 10)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: response)
    ) as transport:
        client.sdk_configuration.async_client = transport
        request = asyncio.create_task(
            async_generate_image(hass, client, "model", "Draw", [])
        )
        await asyncio.wait_for(entered.wait(), 2)
        if cancel:
            request.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await asyncio.wait_for(request, 2)
        assert not transport.is_closed
    assert stream.closed


def test_source_representation_omits_provider_identifiers() -> None:
    """Accidental object formatting does not expose file IDs or URL signatures."""
    sources = ImageGenerationSources(file_ids=("private-file",), urls=(STORAGE_URL,))
    assert "private-file" not in repr(sources)
    assert "test-signature" not in repr(sources)


class _BrokenStream(httpx2.AsyncByteStream):
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield _png()[:8]
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (httpx2.ReadError(STORAGE_URL), ApiErrorKind.CONNECTION),
        (httpx2.ReadTimeout(STORAGE_URL), ApiErrorKind.TIMEOUT),
    ],
)
async def test_stream_failure_is_redacted_and_response_is_closed(
    hass: HomeAssistant, error: Exception, kind: ApiErrorKind
) -> None:
    """Failures after response headers cannot expose storage credentials."""
    stream = _BrokenStream(error)
    client = _url_client()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, stream=stream)
        )
    ) as transport:
        client.sdk_configuration.async_client = transport
        with pytest.raises(ImageDownloadError) as caught:
            await async_generate_image(hass, client, "model", "Draw", [])
    assert classify_api_error(caught.value) is kind
    assert "test-signature" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert stream.closed
