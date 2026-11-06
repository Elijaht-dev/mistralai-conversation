"""Exercise the real SDK speech protocol over a local verified TLS socket."""

import asyncio
import base64
import json

from .realtime_cold_start import prepare_tls


def configure_slow_transport(api):
    """Accelerate heartbeat timing to expose protocol-read backpressure."""
    original_connect = api.websocket_connect

    def connect(*args, **kwargs):
        kwargs["ping_interval"] = 0.01
        kwargs["ping_timeout"] = 0.01
        return original_connect(*args, **kwargs)

    api.websocket_connect = connect


async def exercise_realtime(hass, api, client, status, *, slow=False, alias=False):
    """Prove incremental speech without lazy imports or TLS setup on HA's loop."""
    serve, context = await hass.async_add_executor_job(prepare_tls, api)
    if slow:
        configure_slow_transport(api)
    first_audio = asyncio.Event()
    received = []
    session = {
        "request_id": "test-request",
        "model": "test-model",
        "is_ref_audio": False,
        "voice": "test-voice",
        "response_format": "mp3",
        "streaming_mode": "responsive",
        "expressiveness": 1.0,
    }

    async def handler(socket):
        assert socket.request.path == "/v1/audio/speech/realtime?model=test-model"
        assert socket.request.headers["Authorization"] == "Bearer test-api-key"
        await socket.send(json.dumps({"type": "session.created", "session": session}))
        async for raw in socket:
            message = json.loads(raw)
            received.append(message)
            if message["type"] == "session.update":
                await socket.send(
                    json.dumps(
                        {
                            "type": "session.updated",
                            "session": {
                                **session,
                                **message["session"],
                                **({"voice": "test-voice-slug"} if alias else {}),
                            },
                        }
                    )
                )
            elif message["type"] == "input_text.append":
                await socket.send(
                    json.dumps(
                        {
                            "type": "speech.audio.delta",
                            "audio_data": base64.b64encode(b"test").decode(),
                        }
                    )
                )
            elif message["type"] == "input_text.end":
                await finish_generation(socket, status, slow=slow)

    async def text():
        yield "Hello "
        await first_audio.wait()
        yield "world."

    async with serve(handler, "127.0.0.1", 0, ssl=context) as server:
        client.sdk_configuration.server_url = (
            f"https://localhost:{server.sockets[0].getsockname()[1]}"
        )
        stream = api.async_stream_speech(
            client, "test-model", "test-voice", "mp3", text()
        )
        try:
            assert await anext(stream) == b"test"
        except api.RealtimeError:
            assert alias and status != 200
            assert [message["type"] for message in received] == ["session.update"]
            return
        assert all(message["type"] != "input_text.end" for message in received)
        first_audio.set()
        if slow:
            await asyncio.sleep(0.15)
        try:
            expected = b"test" * (13 if slow and status == 200 else 1)
            assert b"".join([chunk async for chunk in stream]) == expected
        except api.RealtimeError:
            assert status != 200
        else:
            assert status == 200
    assert [message["type"] for message in received] == [
        "session.update",
        "input_text.append",
        "input_text.append",
        "input_text.end",
    ]


async def finish_generation(socket, status, *, slow):
    """Simulate the terminal event and the provider's close behavior."""
    if slow and status == 200:
        for _ in range(12):
            await socket.send(
                json.dumps(
                    {
                        "type": "speech.audio.delta",
                        "audio_data": base64.b64encode(b"test").decode(),
                    }
                )
            )
    event = (
        {"type": "speech.audio.done", "usage": {}}
        if status == 200
        else {"type": "error", "error": {"code": 422, "message": "test error"}}
    )
    await socket.send(json.dumps(event))
    if slow and status == 200:
        await socket.wait_closed()
    elif status == 200:
        # The live endpoint can end TCP without a WebSocket close frame.
        socket.transport.abort()
    else:
        await socket.close()
