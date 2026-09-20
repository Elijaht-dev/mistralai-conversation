"""Exercise the connection adapter with the socket as the only mocked boundary."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from mistralai.client.models import Security

from custom_components.mistral_conversation.api import async_transcribe_realtime
from custom_components.mistral_conversation.const import (
    REALTIME_STT_MODEL,
    ApiErrorKind,
)
from custom_components.mistral_conversation.errors import RealtimeError

from .test_realtime import Socket, audio


@pytest.fixture
def transport(monkeypatch):
    """Provide a real configuration and a mocked, context-managed WebSocket."""
    socket = Socket()
    socket.connect_delay = 0
    socket.recv = AsyncMock(
        return_value=json.dumps(
            {
                "type": "session.created",
                "session": {
                    "request_id": "test-request",
                    "model": REALTIME_STT_MODEL,
                    "audio_format": {"encoding": "pcm_s16le", "sample_rate": 16000},
                },
            }
        )
    )
    client = MagicMock()
    client.sdk_configuration.get_server_details.return_value = (
        "https://api.mistral.ai",
        {},
    )
    client.sdk_configuration.security = Security(api_key="test-api-key")
    client.sdk_configuration.user_agent = "test-agent"
    calls = []

    @asynccontextmanager
    async def connect(url, **kwargs):
        calls.append((url, kwargs))
        await asyncio.sleep(socket.connect_delay)
        try:
            yield socket
        finally:
            await socket.close()

    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.websocket_connect", connect
    )
    return client, socket, calls


async def test_transport_uses_verified_tls_and_sdk_messages(transport):
    client, socket, calls = transport
    result = await async_transcribe_realtime(client, REALTIME_STT_MODEL, audio(b"ab"))
    assert result == "Hello"
    url, kwargs = calls[0]
    assert (
        url
        == f"wss://api.mistral.ai/v1/audio/transcriptions/realtime?model={REALTIME_STT_MODEL}"
    )
    assert kwargs["ssl"].check_hostname
    assert kwargs["additional_headers"]["Authorization"] == "Bearer test-api-key"
    assert socket.sent[0]["type"] == "session.update"
    assert socket.sent[0]["session"]["audio_format"] == {
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
    }
    assert socket.closed


@pytest.mark.parametrize(
    "handshake", ["broken-json", "[]", '{"type":"transcription.done"}']
)
async def test_invalid_handshake_closes_socket(transport, handshake):
    client, socket, _ = transport
    socket.recv.return_value = handshake
    with pytest.raises(RealtimeError):
        await async_transcribe_realtime(client, REALTIME_STT_MODEL, audio(b"ab"))
    assert socket.closed
    assert not socket.sent


@pytest.mark.parametrize("cancel", [False, True])
async def test_handshake_timeout_or_cancel_closes_socket(
    transport, monkeypatch, cancel
):
    client, socket, _ = transport
    started = asyncio.Event()

    async def stalled():
        started.set()
        await asyncio.Event().wait()

    socket.recv.side_effect = stalled
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.SETUP_TIMEOUT_MS", 20
    )
    task = asyncio.create_task(
        async_transcribe_realtime(client, REALTIME_STT_MODEL, audio(b"ab"))
    )
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RealtimeError) as caught:
            await task
        assert caught.value.kind is ApiErrorKind.TIMEOUT
    assert socket.closed
    assert not socket.sent


async def test_reject_insecure_endpoint(transport):
    client, socket, calls = transport
    client.sdk_configuration.get_server_details.return_value = (
        "http://api.mistral.ai",
        {},
    )
    with pytest.raises(RealtimeError, match="secure"):
        await async_transcribe_realtime(client, REALTIME_STT_MODEL, audio(b"ab"))
    assert not calls
    assert not socket.sent


async def test_opening_and_handshake_share_timeout(transport, monkeypatch):
    client, socket, _ = transport
    socket.connect_delay = 0.03
    original_handshake = socket.recv.return_value

    async def delayed_handshake():
        await asyncio.sleep(0.03)
        return original_handshake

    socket.recv.side_effect = delayed_handshake
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.SETUP_TIMEOUT_MS", 50
    )
    with pytest.raises(RealtimeError) as caught:
        await async_transcribe_realtime(client, REALTIME_STT_MODEL, audio(b"ab"))
    assert caught.value.kind is ApiErrorKind.TIMEOUT
    assert socket.closed
    assert not socket.sent
