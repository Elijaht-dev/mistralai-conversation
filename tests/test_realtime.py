"""Realtime transcription lifecycle using real SDK serialization and events."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterable
from unittest.mock import AsyncMock, MagicMock

import pytest
from mistralai.client.models import AudioFormat, RealtimeTranscriptionSession
from mistralai.extra.exceptions import RealtimeTranscriptionException
from mistralai.extra.realtime.connection import RealtimeConnection
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from custom_components.mistral_conversation.api import async_transcribe_realtime
from custom_components.mistral_conversation.const import (
    REALTIME_STT_MODEL,
    ApiErrorKind,
)
from custom_components.mistral_conversation.errors import (
    RealtimeError,
    classify_api_error,
)

pytestmark = pytest.mark.usefixtures("mock_realtime_connect")


def _done(text: str = "Hello") -> dict:
    return {
        "type": "transcription.done",
        "text": text,
        "model": REALTIME_STT_MODEL,
        "language": "en",
        "usage": {},
    }


class Socket:
    """Mock only the socket while exercising the real SDK connection."""

    def __init__(
        self, final_events: list[dict] | None = None, *, hold_final: bool = False
    ) -> None:
        self.sent: list[dict] = []
        self.received: asyncio.Queue[str | None] = asyncio.Queue()
        self.final_events = [_done()] if final_events is None else final_events
        self.closed = False
        self.audio_sent = asyncio.Event()
        self.finished = asyncio.Event()
        self.hold_final = hold_final

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        self.sent.append(payload)
        if payload["type"] == "input_audio.append":
            self.audio_sent.set()
        if payload["type"] == "input_audio.end":
            self.finished.set()
            if self.hold_final:
                return
            for event in self.final_events:
                self.received.put_nowait(json.dumps(event))
            self.received.put_nowait(None)

    async def close(self, **kwargs) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        value = await self.received.get()
        if value is None:
            raise StopAsyncIteration
        return value


def connection(socket: Socket) -> RealtimeConnection:
    return RealtimeConnection(
        socket,
        RealtimeTranscriptionSession(
            request_id="test-request",
            model=REALTIME_STT_MODEL,
            audio_format=AudioFormat(encoding="pcm_s16le", sample_rate=16000),
        ),
    )


def client_for(socket: Socket) -> MagicMock:
    client = MagicMock()
    client.audio.realtime.connect = AsyncMock(return_value=connection(socket))
    return client


async def audio(*chunks: bytes) -> AsyncIterable[bytes]:
    for chunk in chunks:
        yield chunk


async def test_streams_audio_before_input_finishes() -> None:
    """Raw PCM reaches the socket during capture and final text is not duplicated."""
    socket = Socket([{"type": "transcription.text.delta", "text": "Hel"}, _done()])
    client = client_for(socket)

    async def paced_audio():
        yield b"ab"
        await asyncio.wait_for(socket.audio_sent.wait(), 1)
        assert not socket.finished.is_set()
        yield b""
        yield b"cd"

    result = await async_transcribe_realtime(client, REALTIME_STT_MODEL, paced_audio())
    assert result == "Hello"
    assert [item["type"] for item in socket.sent] == [
        "input_audio.append",
        "input_audio.append",
        "input_audio.flush",
        "input_audio.end",
    ]
    assert [base64.b64decode(item["audio"]) for item in socket.sent[:2]] == [
        b"ab",
        b"cd",
    ]
    assert socket.closed


@pytest.mark.parametrize(
    "events",
    [
        [],
        [_done("")],
        [_done("  ")],
        [{"type": "transcription.done"}],
        [{"type": "unexpected", "private": "private-data"}],
        [{"type": "error", "error": {"code": 401, "message": "private-data"}}],
    ],
)
async def test_rejects_invalid_or_missing_final(events) -> None:
    """Partial, malformed and provider-error streams fail closed without raw data."""
    socket = Socket(events)
    with pytest.raises(RealtimeError) as caught:
        await async_transcribe_realtime(
            client_for(socket), REALTIME_STT_MODEL, audio(b"ab")
        )
    assert classify_api_error(caught.value) is ApiErrorKind.API
    assert "private-data" not in str(caught.value)
    assert socket.closed


@pytest.mark.parametrize("chunks", [(), (b"",), (b"ab", b"cd")])
async def test_bounds_input(chunks, monkeypatch) -> None:
    """Empty and excessive audio cannot succeed or send bytes past the limit."""
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.MAX_STT_AUDIO_BYTES", 3
    )
    socket = Socket()
    with pytest.raises(RealtimeError):
        await async_transcribe_realtime(
            client_for(socket), REALTIME_STT_MODEL, audio(*chunks)
        )
    assert not socket.finished.is_set()
    assert socket.closed


async def test_exact_input_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.MAX_STT_AUDIO_BYTES", 2
    )
    assert (
        await async_transcribe_realtime(
            client_for(Socket()), REALTIME_STT_MODEL, audio(b"ab")
        )
        == "Hello"
    )


async def test_send_error_interrupts_receiver() -> None:
    """Upload errors never wait for a server response which cannot arrive."""
    socket = Socket()
    socket.send = AsyncMock(side_effect=OSError("private-data"))
    with pytest.raises(RealtimeError) as caught:
        await asyncio.wait_for(
            async_transcribe_realtime(
                client_for(socket), REALTIME_STT_MODEL, audio(b"ab")
            ),
            1,
        )
    assert caught.value.kind is ApiErrorKind.CONNECTION
    assert "private-data" not in str(caught.value)
    assert socket.closed


async def test_premature_done_cancels_upload() -> None:
    socket = Socket()
    socket.received.put_nowait(json.dumps(_done()))
    stopped = asyncio.Event()

    async def endless_audio():
        try:
            yield b"ab"
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with pytest.raises(RealtimeError, match="before audio"):
        await asyncio.wait_for(
            async_transcribe_realtime(
                client_for(socket), REALTIME_STT_MODEL, endless_audio()
            ),
            1,
        )
    assert stopped.is_set()
    assert socket.closed


async def test_cancellation_closes_socket_and_propagates() -> None:
    socket = Socket()

    async def endless_audio():
        yield b"ab"
        await asyncio.Event().wait()

    task = asyncio.create_task(
        async_transcribe_realtime(
            client_for(socket), REALTIME_STT_MODEL, endless_audio()
        )
    )
    await socket.audio_sent.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert socket.closed


async def test_operation_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.REQUEST_TIMEOUT_MS", 10
    )
    socket = Socket()

    async def endless_audio():
        yield b"ab"
        await asyncio.Event().wait()

    with pytest.raises(RealtimeError) as caught:
        await async_transcribe_realtime(
            client_for(socket), REALTIME_STT_MODEL, endless_audio()
        )
    assert caught.value.kind is ApiErrorKind.TIMEOUT
    assert socket.closed


@pytest.mark.parametrize("cancel", [False, True])
async def test_cancel_or_timeout_while_waiting_for_final(monkeypatch, cancel) -> None:
    """SDK iteration swallowing cancellation cannot turn it into an API failure."""
    socket = Socket(hold_final=True)
    monkeypatch.setattr(
        "custom_components.mistral_conversation.api.REQUEST_TIMEOUT_MS", 50
    )
    task = asyncio.create_task(
        async_transcribe_realtime(client_for(socket), REALTIME_STT_MODEL, audio(b"ab"))
    )
    await socket.finished.wait()
    await asyncio.sleep(0)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RealtimeError) as caught:
            await task
        assert caught.value.kind is ApiErrorKind.TIMEOUT
    assert socket.closed


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, ApiErrorKind.AUTHENTICATION),
        (403, ApiErrorKind.AUTHENTICATION),
        (429, ApiErrorKind.RATE_LIMIT),
        (408, ApiErrorKind.TIMEOUT),
        (504, ApiErrorKind.TIMEOUT),
        (503, ApiErrorKind.CONNECTION),
        (400, ApiErrorKind.API),
    ],
)
async def test_handshake_http_errors(status, kind) -> None:
    client = client_for(Socket())
    wrapped = RealtimeTranscriptionException("private-data")
    wrapped.__cause__ = InvalidStatus(Response(status, "private-data", Headers()))
    client.audio.realtime.connect.side_effect = wrapped
    with pytest.raises(RealtimeError) as caught:
        await async_transcribe_realtime(client, REALTIME_STT_MODEL, audio(b"ab"))
    assert caught.value.kind is kind
    assert "private-data" not in str(caught.value)


async def test_simultaneous_requests_are_independent() -> None:
    first, second = Socket([_done("First")]), Socket([_done("Second")])
    client = MagicMock()
    client.audio.realtime.connect = AsyncMock(
        side_effect=[connection(first), connection(second)]
    )
    results = await asyncio.gather(
        *[
            async_transcribe_realtime(client, REALTIME_STT_MODEL, audio(b"ab"))
            for _ in range(2)
        ]
    )
    assert results == ["First", "Second"]
    assert first.closed and second.closed
