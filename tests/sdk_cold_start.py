"""Run the real SDK in a fresh process with HA's blocking detector enabled."""

import asyncio
import importlib
import json
import logging
import sys
from pathlib import Path

from homeassistant import block_async_io, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame


class BlockingCalls(logging.Handler):
    """Collect HA detections, including those downgraded to debug after repeats."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if "Detected blocking call" in record.msg:
            self.calls.append(record.getMessage().split(" with args", 1)[0])


async def exercise(api, client, endpoint: str) -> None:
    """Exercise one endpoint before any other request can warm the SDK."""
    if endpoint == "models":
        assert await api.async_get_models(client) == []
    elif endpoint == "voices":
        voices = await api.async_get_voices(client)
        assert [(voice.id, voice.name, voice.languages) for voice in voices] == [
            ("test-custom", "Custom", ("fr",)),
            ("test-preset", "Preset", ("en",)),
        ]
    elif endpoint == "chat":
        stream = await client.chat.stream_async(
            model="test-model",
            messages=[{"role": "user", "content": "Hello"}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "test", "schema": {"type": "object"}},
            },
        )
        async with stream:
            chunks = [chunk async for chunk in stream]
        assert chunks[0].data.choices[0].delta.content == "Hello"
    elif endpoint == "stt":
        result = await client.audio.transcriptions.complete_async(
            model="test-model",
            file={"file_name": "test.wav", "content": b"test"},
        )
        assert result.text == "Hello"
    elif endpoint == "tts":
        result = await client.audio.speech.complete_async(
            model="test-model",
            input="Hello",
            voice_id="test-voice",
            stream=True,
        )
        async with result:
            chunks = [chunk async for chunk in result]
        assert chunks[0].data.audio_data == "dGVzdA=="
    elif endpoint.startswith("image_"):
        await exercise_image(api, client, endpoint)
    else:
        raise AssertionError(endpoint)


async def exercise_image(api, client, endpoint: str) -> None:
    """Exercise each image surface as the first request in its process."""
    if endpoint == "image_start":
        sources = await api.async_start_image_generation(
            client, "test-model", "Draw a lighthouse", []
        )
        assert sources.file_ids == ("test-file",)
        assert sources.urls == ("https://files.mistral.ai/generated/test.png",)
    elif endpoint == "image_url":
        response = await api.async_download_image_url(
            client, "https://files.mistral.ai/generated/test.png"
        )
        try:
            if response.status_code == 200:
                assert await response.aread() == b"test-image"
            else:
                assert response.status_code == 422
        finally:
            await response.aclose()
    elif endpoint == "image_download":
        response = await api.async_download_image_file(client, "test-file")
        try:
            assert await response.aread() == b"test-image"
        finally:
            await response.aclose()
    elif endpoint == "image_delete":
        assert await api.async_delete_generated_file(client, "test-file")
    else:
        raise AssertionError(endpoint)


def image_response(httpx2, endpoint: str):
    """Return one mocked SDK response for the image endpoints."""
    if endpoint == "image_start":
        return httpx2.Response(
            200,
            json={
                "conversation_id": "test-conversation",
                "outputs": [
                    {
                        "type": "tool.execution",
                        "name": "image_generation",
                        "arguments": "{}",
                        "info": {
                            "result": json.dumps(
                                {"url": "https://files.mistral.ai/generated/test.png"}
                            )
                        },
                    },
                    {
                        "type": "message.output",
                        "content": [
                            {
                                "type": "tool_file",
                                "tool": "image_generation",
                                "file_id": "test-file",
                            }
                        ],
                    },
                ],
                "usage": {},
            },
        )
    if endpoint in ("image_download", "image_url"):
        return httpx2.Response(200, content=b"test-image")
    if endpoint == "image_delete":
        return httpx2.Response(
            200,
            json={"id": "test-file", "object": "file", "deleted": True},
        )
    raise AssertionError(endpoint)


def assert_image_request(endpoint: str, request) -> None:
    """Assert the SDK used the expected image route and safe request fields."""
    if endpoint == "image_start":
        assert request.url.path == "/v1/conversations"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["store"] is False
        assert body["tools"] == [{"type": "image_generation"}]
    elif endpoint == "image_download":
        assert request.url.path == "/v1/files/test-file/content"
    elif endpoint == "image_delete":
        assert request.url.path == "/v1/files/test-file"
    elif endpoint == "image_url":
        assert request.url.host == "files.mistral.ai"
        assert request.url.path == "/generated/test.png"
        assert "authorization" not in request.headers


async def exercise_realtime_endpoint(hass, api, client, endpoint, status):
    """Select the local TLS fixture matching the first realtime request."""
    realtime = await hass.async_add_executor_job(
        importlib.import_module,
        "tests.realtime_cold_start"
        if endpoint == "realtime"
        else "tests.realtime_speech_cold_start",
    )
    if endpoint == "realtime_tts_slow":
        await realtime.exercise_realtime(hass, api, client, status, slow=True)
    elif endpoint == "realtime_tts_alias":
        await realtime.exercise_realtime(hass, api, client, status, alias=True)
    else:
        await realtime.exercise_realtime(hass, api, client, status)


def voice_response(httpx2, endpoint):
    """Use the actual list and single-voice SDK response schemas."""
    preset = {
        "id": "test-preset",
        "name": "Preset",
        "created_at": "2026-09-01T00:00:00Z",
        "user_id": None,
        "type": "preset",
        "languages": ["en"],
    }
    if endpoint == "realtime_tts_alias":
        return httpx2.Response(
            200,
            json={**preset, "id": "test-voice", "slug": "test-voice-slug"},
        )
    return httpx2.Response(
        200,
        json={
            "data": [
                preset,
                {
                    **preset,
                    "id": "test-custom",
                    "name": "Custom",
                    "user_id": "test-user",
                    "type": "custom",
                    "languages": ["fr"],
                },
            ],
            "next_page_token": None,
        },
    )


async def main() -> None:
    """Exercise the first request without pytest preloading provider modules."""
    hass = HomeAssistant("/tmp/mistral-cold-start")
    frame.async_setup(hass)
    loader.async_setup(hass)
    detections = BlockingCalls()
    logger = logging.getLogger("homeassistant.util.loop")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(detections)
    assert "mistralai.client" not in sys.modules
    block_async_io.enable()
    api = await hass.async_add_executor_job(
        importlib.import_module, "custom_components.mistral_conversation.api"
    )
    httpx2 = await hass.async_add_executor_job(importlib.import_module, "httpx2")

    endpoint, status = sys.argv[1], int(sys.argv[2])
    requests = []

    async def respond(request):
        requests.append(request)
        if status != 200:
            body = {
                "detail": [
                    {"loc": ["body"], "msg": "test error", "type": "value_error"}
                ]
            }
            return httpx2.Response(status, json=body)
        if endpoint == "chat":
            chunk = {
                "id": "test-completion",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ],
            }
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        if endpoint == "stt":
            return httpx2.Response(
                200,
                json={
                    "text": "Hello",
                    "model": "test-model",
                    "usage": {},
                    "language": "en",
                },
            )
        if endpoint == "tts":
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    "event: speech.audio.delta\n"
                    'data: {"type":"speech.audio.delta","audio_data":"dGVzdA=="}'
                    "\n\n"
                ),
            )
        if endpoint in ("voices", "realtime_tts_alias"):
            if endpoint == "realtime_tts_alias":
                assert request.url.path == "/v1/audio/voices/test-voice"
                assert request.headers["Authorization"] == "Bearer test-api-key"
            return voice_response(httpx2, endpoint)
        return (
            image_response(httpx2, endpoint)
            if endpoint.startswith("image_")
            else httpx2.Response(200, json={"object": "list", "data": []})
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as transport:
        api.get_async_client = lambda hass: transport
        resources = await api.async_create_client(hass, "test-api-key")
        client = resources.client
        sync_transport = client.sdk_configuration.client
        try:
            if endpoint.startswith("realtime"):
                await exercise_realtime_endpoint(hass, api, client, endpoint, status)
            else:
                await exercise(api, client, endpoint)
        except api.errors.MistralError:
            assert status != 200
        else:
            assert status == 200 or endpoint in (
                "realtime",
                "realtime_tts",
                "realtime_tts_slow",
                "realtime_tts_alias",
                "image_url",
            )
        finally:
            await api.async_close_client(hass, resources)
        assert sync_transport.is_closed
        assert not transport.is_closed
        assert len(requests) == (
            0 if endpoint in ("realtime", "realtime_tts", "realtime_tts_slow") else 1
        )
        if endpoint.startswith("image_"):
            assert_image_request(endpoint, requests[0])
    await hass.async_stop()
    assert not detections.calls, detections.calls


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
