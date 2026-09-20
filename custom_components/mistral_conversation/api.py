"""Helpers for interacting with the Mistral SDK."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from types import TracebackType
from typing import Protocol, cast
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.ssl import get_default_context
from mistralai.client import Mistral, errors, models, utils

# Load lazy SDK services through HA's integration import executor, before setup.
from mistralai.client.audio import Audio  # noqa: F401
from mistralai.client.chat import Chat  # noqa: F401
from mistralai.client.models import BaseModelCard, FTModelCard
from mistralai.client.models_ import Models  # noqa: F401
from mistralai.extra.exceptions import RealtimeTranscriptionException
from mistralai.extra.realtime.connection import (
    RealtimeConnection,
    UnknownRealtimeEvent,
    parse_realtime_event,
)
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import InvalidStatus, WebSocketException

from .const import (
    LOGGER,
    MAX_STT_AUDIO_BYTES,
    MAX_VOICES,
    REQUEST_TIMEOUT_MS,
    SETUP_TIMEOUT_MS,
    VOICE_LIST_PAGE_SIZE,
    ApiErrorKind,
    MistralModel,
    MistralVoice,
)
from .errors import RealtimeError

# SDK 2.10.0's lazy exports do not cache resolved attributes. Merely importing
# their modules still calls import_module (with relative names) on every access.
# Bind the original public objects once while HA imports this integration in its
# import executor. No SDK function is replaced and no blocking warning is muted.
# Limit model loading to the request/response types used by our five endpoints.
for _name in (
    "Security",
    "ListModelsV1ModelsGetRequest",
    "ModelList",
    "ChatCompletionStreamRequest",
    "ChatCompletionStreamRequestStop",
    "ChatCompletionStreamRequestMessage",
    "ChatCompletionStreamRequestTool",
    "ChatCompletionStreamRequestToolChoice",
    "ResponseFormat",
    "Prediction",
    "GuardrailConfig",
    "CompletionEvent",
    "AudioTranscriptionRequest",
    "File",
    "TimestampGranularity",
    "TranscriptionResponse",
    "SpeechRequest",
    "SpeechResponse",
    "SpeechStreamEvents",
    "ListVoicesV1AudioVoicesGetRequest",
    "VoiceListResponse",
    "AudioFormat",
    "RealtimeTranscriptionError",
    "RealtimeTranscriptionSessionCreated",
    "TranscriptionStreamDone",
):
    setattr(models, _name, getattr(models, _name))
for _module in (errors, utils):
    for _name in _module.__all__:
        setattr(_module, _name, getattr(_module, _name))


class _ClosableMistralClient(Protocol):
    """Typed context-manager surface missing from the generated SDK hints."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> object:
        """Close synchronous SDK-owned resources."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> object:
        """Close asynchronous SDK-owned resources."""
        ...


def _create_client(api_key: str, http_client: httpx.AsyncClient) -> Mistral:
    """Construct and prime the SDK in an executor without making requests."""
    client = Mistral(
        api_key=api_key,
        async_client=http_client,
    )
    try:
        # Mistral also creates a sync HTTP client and lazily initializes services.
        # Do both here, including the audio service's speech/transcription/voices.
        _ = client.models, client.chat, client.audio.realtime
        # Populate HA's cached, verified TLS context in this executor as well.
        get_default_context()
    except BaseException:
        with suppress(Exception):
            cast(_ClosableMistralClient, client).__exit__(None, None, None)
        raise
    return client


async def async_create_client(hass: HomeAssistant, api_key: str) -> Mistral:
    """Keep HA transport access on the loop and blocking SDK setup off it."""
    future = hass.async_add_executor_job(
        _create_client, api_key, get_async_client(hass)
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Cancelling the await cannot stop the worker; reclaim its eventual client.
        with suppress(Exception):
            client = await future
            await async_close_client(hass, client)
        raise


async def async_close_client(hass: HomeAssistant, client: Mistral) -> None:
    """Release resources owned by a Mistral client.

    The asynchronous HTTP transport is supplied by Home Assistant. The SDK
    detaches from that transport without closing Home Assistant's shared client.
    """
    closable_client = cast(_ClosableMistralClient, client)
    with suppress(Exception):
        await hass.async_add_executor_job(closable_client.__exit__, None, None, None)
    with suppress(Exception):
        await closable_client.__aexit__(None, None, None)


def _realtime_error(err: Exception) -> RealtimeError:
    """Classify structured transport failures without exposing provider content."""
    kind = ApiErrorKind.API
    cause: BaseException | None = err
    while cause is not None:
        if isinstance(cause, InvalidStatus):
            status = cause.response.status_code
            if status in (401, 403):
                kind = ApiErrorKind.AUTHENTICATION
            elif status == 429:
                kind = ApiErrorKind.RATE_LIMIT
            elif status in (408, 504):
                kind = ApiErrorKind.TIMEOUT
            elif status >= 500:
                kind = ApiErrorKind.CONNECTION
            break
        if isinstance(cause, TimeoutError):
            kind = ApiErrorKind.TIMEOUT
            break
        if isinstance(cause, OSError | WebSocketException):
            kind = ApiErrorKind.CONNECTION
            break
        cause = cause.__cause__
    return RealtimeError("Realtime transcription failed", kind)


@asynccontextmanager
async def _connect_realtime(
    client: Mistral, model: str
) -> AsyncGenerator[RealtimeConnection]:
    """Open a verified HA-safe socket, then use the SDK's protocol objects."""
    # SDK 2.10.0 connect() cannot accept a prepared SSLContext. Its default
    # websockets transport loads certificates on HA's loop. Keep this narrow
    # connection adapter until that public SDK API can receive HA's context;
    # all audio messages and event parsing remain owned by the official SDK.
    config = client.sdk_configuration
    base_url, _ = config.get_server_details()
    endpoint = utils.generate_url(base_url, "/v1/audio/transcriptions/realtime", None)
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        raise RealtimeError("Realtime transcription requires a secure cloud endpoint")
    security = config.security() if callable(config.security) else config.security
    resolved_security = utils.get_security_from_env(security, models.Security)
    headers: dict[str, str] = {}
    query = {"model": model}
    if resolved_security is not None:
        headers, security_query = utils.get_security(resolved_security)
        query.update(
            {key: values[0] for key, values in security_query.items() if values}
        )
    url = urlunparse(parsed._replace(scheme="wss", query=urlencode(query)))
    setup_deadline = asyncio.get_running_loop().time() + SETUP_TIMEOUT_MS / 1000
    async with websocket_connect(
        url,
        additional_headers=headers,
        user_agent_header=config.user_agent,
        ssl=get_default_context(),
        open_timeout=SETUP_TIMEOUT_MS / 1000,
    ) as websocket:
        async with asyncio.timeout_at(setup_deadline):
            event = parse_realtime_event(json.loads(await websocket.recv()))
            if not isinstance(event, models.RealtimeTranscriptionSessionCreated):
                raise RealtimeError("Invalid Realtime session handshake")
            connection = RealtimeConnection(websocket, event.session)
            await connection.update_session(
                models.AudioFormat(encoding="pcm_s16le", sample_rate=16000)
            )
        yield connection


async def _send_realtime_audio(
    connection: RealtimeConnection, stream: AsyncIterable[bytes]
) -> None:
    """Send bounded PCM chunks without retaining the recording."""
    size = 0
    async for chunk in stream:
        size += len(chunk)
        if size > MAX_STT_AUDIO_BYTES:
            raise RealtimeError("STT audio exceeded the local size limit")
        if chunk:
            await connection.send_audio(chunk)
    if not size:
        raise RealtimeError("STT audio was empty")
    await connection.flush_audio()
    await connection.end_audio()


async def _receive_realtime_text(
    connection: RealtimeConnection, sender: asyncio.Task[None]
) -> str:
    """Require a well-formed final response after all audio has been sent."""
    async for event in connection:
        if isinstance(event, UnknownRealtimeEvent):
            raise RealtimeError("Invalid Realtime transcription event")
        if isinstance(event, models.RealtimeTranscriptionError):
            # error.code is documented as an internal code, not an HTTP status.
            raise RealtimeError("Realtime transcription was rejected")
        if isinstance(event, models.TranscriptionStreamDone):
            if not sender.done():
                raise RealtimeError("Realtime transcription ended before audio input")
            sender.result()
            if not event.text.strip():
                raise RealtimeError("Realtime transcription returned empty text")
            return event.text
    raise RealtimeError("Realtime transcription ended without a final result")


async def _transcribe_realtime(
    client: Mistral, model: str, stream: AsyncIterable[bytes]
) -> str:
    """Supervise upload and download so neither can mask the other's failure."""
    async with _connect_realtime(client, model) as connection:
        sender = asyncio.create_task(_send_realtime_audio(connection, stream))
        receiver = asyncio.create_task(_receive_realtime_text(connection, sender))
        try:
            done, _ = await asyncio.wait(
                (sender, receiver), return_when=asyncio.FIRST_COMPLETED
            )
            if sender in done:
                sender.result()
            # SDK event iteration consumes CancelledError. Waiting without
            # forwarding cancellation keeps it on this supervisor; finally
            # explicitly cancels and retrieves both tasks' outcomes.
            await asyncio.wait((receiver,))
            return receiver.result()
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)


async def async_transcribe_realtime(
    client: Mistral, model: str, stream: AsyncIterable[bytes]
) -> str:
    """Transcribe a bounded Assist PCM stream using the official Realtime SDK."""
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_MS / 1000):
            return await _transcribe_realtime(client, model, stream)
    except (
        RealtimeTranscriptionException,
        WebSocketException,
        OSError,
        ValueError,
        RuntimeError,
    ) as err:
        raise _realtime_error(err) from None


def _optional_string(value: object) -> str | None:
    """Return a non-empty SDK string value, ignoring its UNSET sentinel."""
    return value if isinstance(value, str) and value else None


def _optional_datetime(value: object) -> datetime | None:
    """Return an SDK datetime value, ignoring its UNSET sentinel."""
    return value if isinstance(value, datetime) else None


def _parse_model(model: BaseModelCard | FTModelCard) -> MistralModel | None:
    """Convert a generated SDK model card into stable integration metadata."""
    if isinstance(model, FTModelCard) and model.archived:
        return None

    capabilities = model.capabilities
    if not capabilities.completion_chat:
        return None

    return MistralModel(
        id=model.id,
        name=_optional_string(model.name),
        description=_optional_string(model.description),
        aliases=tuple(alias for alias in model.aliases or [] if alias),
        function_calling=bool(capabilities.function_calling),
        reasoning=bool(capabilities.reasoning),
        vision=bool(capabilities.vision),
        ocr=bool(capabilities.ocr),
        max_context_length=model.max_context_length,
        default_temperature=(
            model.default_model_temperature
            if isinstance(model.default_model_temperature, float | int)
            else None
        ),
        deprecation=_optional_datetime(model.deprecation),
        replacement_model=_optional_string(model.deprecation_replacement_model),
    )


async def async_get_models(client: Mistral) -> list[MistralModel]:
    """Return chat-capable, non-archived models available to the account."""
    response = await client.models.list_async(timeout_ms=SETUP_TIMEOUT_MS)
    models: list[MistralModel] = []

    for provider_model in response.data or []:
        if not isinstance(provider_model, BaseModelCard | FTModelCard):
            LOGGER.debug(
                "Ignoring an unsupported Mistral model-card variant: %s",
                type(provider_model).__name__,
            )
            continue
        if (model := _parse_model(provider_model)) is not None:
            models.append(model)

    return sorted(models, key=lambda model: model.id.casefold())


async def async_get_voices(client: Mistral) -> list[MistralVoice]:
    """Return preset and custom TTS voices available to the account."""
    voices: list[MistralVoice] = []
    offset = 0

    while offset < MAX_VOICES:
        page_size = min(VOICE_LIST_PAGE_SIZE, MAX_VOICES - offset)
        response = await client.audio.voices.list_async(
            limit=page_size,
            offset=offset,
            type_="all",
            timeout_ms=SETUP_TIMEOUT_MS,
        )
        if not response.items:
            break

        voices.extend(
            MistralVoice(
                id=voice.id,
                name=voice.name,
                languages=tuple(voice.languages or []),
            )
            for voice in response.items
            if voice.id and voice.name
        )
        offset += len(response.items)
        if offset >= response.total:
            break

    return sorted(voices, key=lambda voice: (voice.name.casefold(), voice.id))


async def async_validate_api_key(
    hass: HomeAssistant, api_key: str
) -> list[MistralModel]:
    """Validate an API key and return available chat models."""
    client = await async_create_client(hass, api_key)
    try:
        return await async_get_models(client)
    finally:
        await async_close_client(hass, client)
