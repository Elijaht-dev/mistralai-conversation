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
    else:
        raise AssertionError(endpoint)


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
    httpx = await hass.async_add_executor_job(importlib.import_module, "httpx")

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
            return httpx.Response(status, json=body)
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
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        if endpoint == "stt":
            return httpx.Response(
                200,
                json={
                    "text": "Hello",
                    "model": "test-model",
                    "usage": {},
                    "language": "en",
                },
            )
        if endpoint == "tts":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    "event: speech.audio.delta\n"
                    'data: {"type":"speech.audio.delta","audio_data":"dGVzdA=="}'
                    "\n\n"
                ),
            )
        if endpoint == "voices":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "test-preset",
                            "name": "Preset",
                            "created_at": "2026-09-01T00:00:00Z",
                            "user_id": None,
                            "type": "preset",
                            "languages": ["en"],
                        },
                        {
                            "id": "test-custom",
                            "name": "Custom",
                            "created_at": "2026-09-01T00:00:00Z",
                            "user_id": "test-user",
                            "type": "custom",
                            "languages": ["fr"],
                        },
                    ],
                    "total": 2,
                    "page": 0,
                    "page_size": 100,
                    "total_pages": 1,
                },
            )
        return httpx.Response(200, json={"object": "list", "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        api.get_async_client = lambda hass: transport
        client = await api.async_create_client(hass, "test-api-key")
        sync_transport = client.sdk_configuration.client
        try:
            if endpoint == "realtime":
                realtime = await hass.async_add_executor_job(
                    importlib.import_module, "tests.realtime_cold_start"
                )
                await realtime.exercise_realtime(hass, api, client, status)
            else:
                await exercise(api, client, endpoint)
        except api.errors.MistralError:
            assert status != 200
        else:
            assert status == 200 or endpoint == "realtime"
        finally:
            await api.async_close_client(hass, client)
        assert sync_transport.is_closed
        assert not transport.is_closed
        assert len(requests) == (0 if endpoint == "realtime" else 1)
    await hass.async_stop()
    assert not detections.calls, detections.calls


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
