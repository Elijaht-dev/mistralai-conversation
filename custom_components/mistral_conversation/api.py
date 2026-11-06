"""Helpers for interacting with the Mistral SDK."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import aclosing, asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Protocol, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunparse

import httpcore2  # noqa: F401 -- HTTPX2 loads its transport lazily.
import httpx2
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util.ssl import get_default_context
from mistralai.client import Mistral, errors, models, utils

# Load lazy SDK services through HA's integration import executor, before setup.
from mistralai.client.audio import Audio  # noqa: F401
from mistralai.client.beta import Beta  # noqa: F401
from mistralai.client.chat import Chat  # noqa: F401
from mistralai.client.conversations import Conversations  # noqa: F401
from mistralai.client.files import Files  # noqa: F401
from mistralai.client.models import BaseModelCard, FTModelCard
from mistralai.client.models_ import Models  # noqa: F401
from mistralai.extra.exceptions import RealtimeTranscriptionException
from mistralai.extra.realtime.connection import (
    RealtimeConnection,
    UnknownRealtimeEvent,
    parse_realtime_event,
)
from mistralai.extra.realtime.speech import (
    RealtimeSpeechConnection,
    RealtimeSpeechEvent,
    parse_realtime_speech_event,
)
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from .const import (
    IMAGE_MAX_TOKENS,
    LOGGER,
    MAX_STT_AUDIO_BYTES,
    MAX_TTS_AUDIO_BYTES,
    MAX_TTS_TEXT_LENGTH,
    MAX_VOICES,
    REQUEST_TIMEOUT_MS,
    SETUP_TIMEOUT_MS,
    VOICE_LIST_PAGE_SIZE,
    ApiErrorKind,
    MistralModel,
    MistralVoice,
)
from .errors import (
    ImageDownloadError,
    RealtimeError,
    SpeechStreamError,
    classify_api_error,
)

# SDK 3.7.0's lazy exports do not cache resolved attributes. Merely importing
# their modules still calls import_module (with relative names) on every access.
# Bind the original public objects once while HA imports this integration in its
# import executor. No SDK function is replaced and no blocking warning is muted.
# Limit model loading to the request/response types used by our endpoints.
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
    "IPAPhoneme",
    "ConversationHistoryUnion",
    "ConversationHistoryUnionTypedDict",
    "SpeechResponse",
    "SpeechStreamEvents",
    "SpeechStreamAudioDelta",
    "SpeechStreamDone",
    "AudioSpeechCapabilities",
    "RealtimeSpeechSessionCreated",
    "RealtimeSpeechError",
    "RealtimeSpeechSessionUpdated",
    "RealtimeSpeechSessionUpdatePayload",
    "RealtimeSpeechSessionUpdateMessage",
    "RealtimeSpeechInputTextAppend",
    "RealtimeSpeechInputTextEnd",
    "AudioV2VoicesListRequest",
    "AudioV2VoicesListResponse",
    "VoiceListPage",
    "GetVoiceV1AudioVoicesVoiceIDGetRequest",
    "VoiceResponse",
    "VoiceGender",
    "VoiceKind",
    "AudioFormat",
    "RealtimeTranscriptionError",
    "RealtimeTranscriptionSessionCreated",
    "RealtimeTranscriptionSessionUpdated",
    "TranscriptionStreamDone",
    "CompletionArgs",
    "ConversationResponse",
    "MessageInputEntry",
    "MessageOutputEntry",
    "TextChunk",
    "ImageURLChunk",
    "ImageGenerationTool",
    "ToolFileChunk",
    "ToolExecutionEntry",
    "DeleteFileResponse",
    "ConversationInputs",
    "ConversationRequest",
    "ConversationRequestTool",
    "FilesAPIRoutesDownloadFileRequest",
    "FilesAPIRoutesDeleteFileRequest",
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


@dataclass(slots=True)
class MistralClientResources:
    """Keep ownership of the legacy transport separate from HA's shared one."""

    client: Mistral
    owned_http_client: httpx2.AsyncClient | None = None


@dataclass(frozen=True, slots=True)
class ImageGenerationSources:
    """Generated Files API IDs and storage URLs, excluded from representations."""

    file_ids: tuple[str, ...] = field(default=(), repr=False)
    urls: tuple[str, ...] = field(default=(), repr=False)


def _create_client(api_key: str, http_client: httpx2.AsyncClient) -> Mistral:
    """Construct and prime the SDK in an executor without making requests."""
    client = Mistral(
        api_key=api_key,
        async_client=http_client,
    )
    try:
        # Mistral also creates a sync HTTP client and lazily initializes services.
        # Do both here, including the audio service's speech/transcription/voices.
        _ = (
            client.models,
            client.chat,
            client.audio.realtime,
            client.audio.realtime_speech,
            client.beta.conversations,
            client.files,
        )
        # Populate HA's cached, verified TLS context in this executor as well.
        get_default_context()
    except BaseException:
        with suppress(Exception):
            cast(_ClosableMistralClient, client).__exit__(None, None, None)
        raise
    return client


def _create_http_client() -> httpx2.AsyncClient:
    """Construct a verified HTTPX2 transport for older HA releases off the loop."""
    return httpx2.AsyncClient(verify=get_default_context(), follow_redirects=True)


async def async_create_client(
    hass: HomeAssistant, api_key: str
) -> MistralClientResources:
    """Keep HA transport access on the loop and blocking SDK setup off it."""
    shared_client = get_async_client(hass)

    async def create() -> MistralClientResources:
        owned_http_client: httpx2.AsyncClient | None = None
        if isinstance(shared_client, httpx2.AsyncClient):
            http_client = shared_client
        else:
            owned_http_client = await hass.async_add_executor_job(_create_http_client)
            http_client = owned_http_client
        try:
            client = await hass.async_add_executor_job(
                _create_client, api_key, http_client
            )
        except BaseException:
            if owned_http_client is not None:
                with suppress(Exception):
                    await owned_http_client.aclose()
            raise
        return MistralClientResources(client, owned_http_client)

    future = asyncio.create_task(create())
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Cancelling the await cannot stop the worker; reclaim its eventual client.
        with suppress(Exception):
            resources = await future
            await async_close_client(hass, resources)
        raise


async def async_close_client(
    hass: HomeAssistant, resources: MistralClientResources
) -> None:
    """Release resources owned by a Mistral client.

    The SDK detaches from supplied transports. Close only the compatibility
    transport owned by this integration, never Home Assistant's shared client.
    """
    closable_client = cast(_ClosableMistralClient, resources.client)
    with suppress(Exception):
        await hass.async_add_executor_job(closable_client.__exit__, None, None, None)
    with suppress(Exception):
        await closable_client.__aexit__(None, None, None)
    if resources.owned_http_client is not None:
        with suppress(Exception):
            await resources.owned_http_client.aclose()


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


def _realtime_url(client: Mistral, model: str, path: str) -> tuple[str, dict[str, str]]:
    """Resolve the configured secure endpoint and SDK authentication."""
    config = client.sdk_configuration
    base_url, _ = config.get_server_details()
    endpoint = utils.generate_url(base_url, path, None)
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        raise RealtimeError("Realtime requires a secure cloud endpoint")
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
    return url, headers


@asynccontextmanager
async def _connect_realtime(
    client: Mistral, model: str
) -> AsyncGenerator[RealtimeConnection]:
    """Open a verified HA-safe socket, then use the SDK's protocol objects."""
    # SDK 3.7.0 connect() cannot accept a prepared SSLContext. Its default
    # websockets transport loads certificates on HA's loop. Keep this narrow
    # connection adapter until that public SDK API can receive HA's context;
    # all audio messages and event parsing remain owned by the official SDK.
    url, headers = _realtime_url(client, model, "/v1/audio/transcriptions/realtime")
    setup_deadline = asyncio.get_running_loop().time() + SETUP_TIMEOUT_MS / 1000
    async with websocket_connect(
        url,
        additional_headers=headers,
        user_agent_header=client.sdk_configuration.user_agent,
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
            updated = parse_realtime_event(json.loads(await websocket.recv()))
            if (
                not isinstance(updated, models.RealtimeTranscriptionSessionUpdated)
                or updated.session.audio_format.encoding != "pcm_s16le"
                or updated.session.audio_format.sample_rate != 16000
            ):
                raise RealtimeError("Invalid Realtime session configuration")
        try:
            yield connection
        finally:
            await connection.close()


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


async def _speech_voice_matches(
    client: Mistral, requested: str, acknowledged: str
) -> bool:
    """Accept an ID/slug pair only when the official voice record links them."""
    if requested == acknowledged:
        return True
    voice = await client.audio.voices.get_async(
        voice_id=requested, timeout_ms=SETUP_TIMEOUT_MS
    )
    aliases = {voice.id}
    if isinstance(voice.slug, str) and voice.slug:
        aliases.add(voice.slug)
    return requested in aliases and acknowledged in aliases


@asynccontextmanager
async def _connect_realtime_speech(
    client: Mistral, model: str, voice_id: str, output_format: models.SpeechOutputFormat
) -> AsyncGenerator[RealtimeSpeechConnection]:
    """Use HA's verified TLS context with the official SDK speech protocol."""
    url, headers = _realtime_url(client, model, "/v1/audio/speech/realtime")
    deadline = asyncio.get_running_loop().time() + SETUP_TIMEOUT_MS / 1000
    async with websocket_connect(
        url,
        additional_headers=headers,
        user_agent_header=client.sdk_configuration.user_agent,
        ssl=get_default_context(),
        open_timeout=SETUP_TIMEOUT_MS / 1000,
        max_queue=1,
    ) as websocket:
        async with asyncio.timeout_at(deadline):
            created = parse_realtime_speech_event(json.loads(await websocket.recv()))
            if not isinstance(created, models.RealtimeSpeechSessionCreated):
                raise SpeechStreamError("tts_response_invalid")
            connection = RealtimeSpeechConnection(websocket, created.session)
            await connection.update_session(
                voice_id=voice_id,
                response_format=output_format,
                streaming_mode="responsive",
            )
            updated = parse_realtime_speech_event(json.loads(await websocket.recv()))
            if (
                not isinstance(updated, models.RealtimeSpeechSessionUpdated)
                or updated.session.is_ref_audio
                or updated.session.response_format != output_format
                or updated.session.streaming_mode != "responsive"
                or not isinstance(updated.session.voice, str)
                or not await _speech_voice_matches(
                    client, voice_id, updated.session.voice
                )
            ):
                raise SpeechStreamError("tts_response_invalid")
        try:
            yield connection
        finally:
            await connection.close()


async def _send_speech_text(
    connection: RealtimeSpeechConnection, text: AsyncGenerator[str]
) -> None:
    """Forward bounded deltas verbatim and end exactly one generation."""
    size = 0
    async for delta in text:
        if not isinstance(delta, str):
            raise SpeechStreamError("tts_response_invalid")
        size += len(delta)
        if size > MAX_TTS_TEXT_LENGTH:
            raise SpeechStreamError("tts_text_too_long")
        if delta:
            await connection.send_text(delta)
    if not size:
        raise SpeechStreamError("tts_response_empty")
    await connection.end()


@dataclass(slots=True)
class _SpeechAudioBuffer:
    """Coalesce bounded audio without pausing WebSocket control frames.

    The receiver enforces the cumulative 25 MiB output limit before every
    append. A slow HA converter may retain that much audio, but never an
    unbounded queue of provider deltas. Fast readers receive bytes immediately.
    """

    _data: bytearray = field(default_factory=bytearray, repr=False)
    _ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def put(self, chunk: bytes) -> None:
        """Append already validated audio without blocking protocol reads."""
        self._data.extend(chunk)
        self._ready.set()

    def empty(self) -> bool:
        """Return whether all received bytes have been consumed."""
        return not self._data

    def get_nowait(self) -> bytes:
        """Transfer buffered bytes atomically on the event loop."""
        data, self._data = self._data, bytearray()
        self._ready.clear()
        return bytes(data)

    async def get(self) -> bytes:
        """Wait until the network receiver has audio for Home Assistant."""
        await self._ready.wait()
        return self.get_nowait()


async def _receive_speech_audio(
    connection: RealtimeSpeechConnection,
    sender: asyncio.Task[None],
    queue: _SpeechAudioBuffer,
) -> None:
    """Require the provider's final event, then close this one-generation socket."""
    size = 0
    # The SDK annotates events() as AsyncIterator, but implements an async
    # generator. Own its closure explicitly when we stop at the terminal event.
    events_generator = cast(AsyncGenerator[RealtimeSpeechEvent], connection.events())
    async with aclosing(events_generator) as events:
        async for event in events:
            if isinstance(event, models.RealtimeSpeechError):
                # Internal provider error codes are not HTTP authentication statuses.
                raise RealtimeError("Realtime speech was rejected")
            if isinstance(event, models.SpeechStreamAudioDelta):
                chunk = base64.b64decode(event.audio_data, validate=True)
                size += len(chunk)
                if size > MAX_TTS_AUDIO_BYTES:
                    raise SpeechStreamError("tts_audio_too_large")
                if chunk:
                    queue.put(chunk)
            elif isinstance(event, models.SpeechStreamDone):
                if not sender.done():
                    raise SpeechStreamError("tts_response_invalid")
                sender.result()
                if not size:
                    raise SpeechStreamError("tts_response_empty")
                # Match the SDK's speak_stream terminal-event contract. The live
                # endpoint can close TCP after done without a WS close frame.
                # Close from our side, rather than reading beyond the generation.
                await connection.close()
                return
            else:
                raise SpeechStreamError("tts_response_invalid")
    # SDK 3.7's event iterator consumes CancelledError. Restore cancellation so
    # the owning request deadline cannot be mistaken for an invalid response.
    current_task = asyncio.current_task()
    if current_task is not None and current_task.cancelling():
        raise asyncio.CancelledError
    raise SpeechStreamError("tts_response_invalid")


def _speech_transport_error(err: Exception) -> RealtimeError:
    """Keep close-frame reasons, provider content and credentials private."""
    if isinstance(err, errors.MistralError | errors.NoResponseError | httpx2.HTTPError):
        return RealtimeError("Realtime speech failed", classify_api_error(err))
    cause: BaseException | None = err
    while cause is not None:
        if (
            isinstance(cause, ConnectionClosed)
            and cause.rcvd is not None
            and cause.rcvd.code in (1003, 1007, 1008)
        ):
            return RealtimeError("Realtime speech request was rejected")
        cause = cause.__cause__
    classified = _realtime_error(err)
    return RealtimeError("Realtime speech failed", classified.kind)


@dataclass(frozen=True, slots=True)
class _RealtimeSpeechRequest:
    """Validated provider settings and the owned incremental text input."""

    model: str
    voice_id: str
    output_format: models.SpeechOutputFormat
    text: AsyncGenerator[str]


async def _run_realtime_speech(
    client: Mistral,
    request: _RealtimeSpeechRequest,
    queue: _SpeechAudioBuffer,
) -> None:
    """Supervise both directions under a deadline independent of the consumer."""
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_MS / 1000):
            async with _connect_realtime_speech(
                client, request.model, request.voice_id, request.output_format
            ) as connection:
                sender = asyncio.create_task(
                    _send_speech_text(connection, request.text)
                )
                receiver = asyncio.create_task(
                    _receive_speech_audio(connection, sender, queue)
                )
                try:
                    await asyncio.gather(sender, receiver)
                finally:
                    sender.cancel()
                    receiver.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
    except RealtimeError:
        raise
    except binascii.Error, ValueError, TypeError:
        raise SpeechStreamError("tts_response_invalid") from None
    except Exception as err:
        raise _speech_transport_error(err) from None
    finally:
        with suppress(Exception):
            await request.text.aclose()


async def async_stream_speech(
    client: Mistral,
    model: str,
    voice_id: str,
    output_format: models.SpeechOutputFormat,
    text: AsyncGenerator[str],
) -> AsyncGenerator[bytes]:
    """Start bounded speech before the complete text or audio is available."""
    queue = _SpeechAudioBuffer()
    producer = asyncio.create_task(
        _run_realtime_speech(
            client, _RealtimeSpeechRequest(model, voice_id, output_format, text), queue
        )
    )
    pending: asyncio.Task[bytes] | None = None
    try:
        while True:
            if producer.done():
                producer.result()
                while not queue.empty():
                    yield queue.get_nowait()
                return
            pending = asyncio.create_task(queue.get())
            await asyncio.wait((producer, pending), return_when=asyncio.FIRST_COMPLETED)
            if producer.done():
                producer.result()
            if pending.done():
                chunk = pending.result()
                pending = None
                yield chunk
            else:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                pending = None
    finally:
        producer.cancel()
        if pending is not None:
            pending.cancel()
        await asyncio.gather(
            producer,
            *((pending,) if pending is not None else ()),
            return_exceptions=True,
        )
        # A task cancelled before its first step never enters its own finally.
        with suppress(Exception):
            await text.aclose()


def _optional_datetime(value: object) -> datetime | None:
    """Return an SDK datetime value, ignoring its UNSET sentinel."""
    return value if isinstance(value, datetime) else None


def _parse_model(model: BaseModelCard | FTModelCard) -> MistralModel | None:
    """Convert a generated SDK model card into stable integration metadata."""
    if isinstance(model, FTModelCard) and model.archived:
        return None

    capabilities = model.capabilities
    speech = (
        model.audio_speech_capabilities if isinstance(model, BaseModelCard) else None
    )

    return MistralModel(
        id=model.id,
        name=_optional_string(model.name),
        description=_optional_string(model.description),
        aliases=tuple(alias for alias in model.aliases or [] if alias),
        completion_chat=bool(capabilities.completion_chat),
        audio_transcription=bool(capabilities.audio_transcription),
        audio_transcription_realtime=bool(capabilities.audio_transcription_realtime),
        audio_speech=bool(capabilities.audio_speech),
        audio_speech_modes=(
            tuple(speech.modes)
            if isinstance(speech, models.AudioSpeechCapabilities)
            and speech.modes is not None
            else None
        ),
        audio_speech_formats=(
            tuple(speech.response_formats)
            if isinstance(speech, models.AudioSpeechCapabilities)
            and speech.response_formats is not None
            else None
        ),
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
    """Return non-archived models with their authoritative usage capabilities."""
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
    voices: dict[str, MistralVoice] = {}
    page_token: str | None = None
    seen_tokens: set[str] = set()
    received = 0
    while received < MAX_VOICES:
        response = await client.audio.voices.search_async(
            page_size=min(VOICE_LIST_PAGE_SIZE, MAX_VOICES - received),
            page_token=page_token,
            type_="all",
            timeout_ms=SETUP_TIMEOUT_MS,
        )
        if response is None or not response.result.data:
            break
        for voice in response.result.data[: MAX_VOICES - received]:
            if voice.id and voice.name:
                voices.setdefault(
                    voice.id,
                    MistralVoice(
                        id=voice.id,
                        name=voice.name,
                        languages=tuple(voice.languages or []),
                    ),
                )
        received += len(response.result.data)
        page_token = _optional_string(response.result.next_page_token)
        if page_token is None or page_token in seen_tokens:
            break
        seen_tokens.add(page_token)

    return sorted(voices.values(), key=lambda voice: (voice.name.casefold(), voice.id))


async def async_start_image_generation(
    client: Mistral, model: str, prompt: str, image_urls: list[str]
) -> ImageGenerationSources:
    """Return only generated files and structured image-tool result URLs."""
    content: list[models.MessageInputContentChunks] = [models.TextChunk(text=prompt)]
    content.extend(models.ImageURLChunk(image_url=url) for url in image_urls)
    inputs: list[models.InputEntries] = [
        models.MessageInputEntry(role="user", content=content)
    ]
    response = await client.beta.conversations.start_async(
        inputs=inputs,
        instructions=(
            "Generate exactly one image with the image_generation tool from the "
            "user's request. Use supplied images as visual references."
        ),
        model=model,
        tools=[models.ImageGenerationTool()],
        completion_args=models.CompletionArgs(max_tokens=IMAGE_MAX_TOKENS),
        store=False,
        retries=None,
        timeout_ms=REQUEST_TIMEOUT_MS,
    )

    file_ids: list[str] = []
    seen: set[str] = set()
    urls: dict[str, None] = {}
    for output in response.outputs:
        if isinstance(output, models.ToolExecutionEntry):
            url = _image_tool_result_url(output)
            if url is not None:
                urls[url] = None
            continue
        if not isinstance(output, models.MessageOutputEntry) or not isinstance(
            output.content, list
        ):
            continue
        for chunk in output.content:
            if (
                isinstance(chunk, models.ToolFileChunk)
                and chunk.tool == "image_generation"
                and chunk.file_id
                and chunk.file_id not in seen
            ):
                seen.add(chunk.file_id)
                file_ids.append(chunk.file_id)
    return ImageGenerationSources(file_ids=tuple(file_ids), urls=tuple(urls))


def _image_tool_result_url(output: models.ToolExecutionEntry) -> str | None:
    """Ignore assistant links, other tools and malformed result metadata."""
    if output.name != "image_generation" or not output.info:
        return None
    result = output.info.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if isinstance(result, dict):
        url = result.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def _image_download_url(value: str) -> httpx2.URL:
    """Restrict tool URLs to HTTPS provider storage with no embedded identity."""
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        raise ImageDownloadError
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise ImageDownloadError
        host = parsed.hostname or ""
        azure_blob = re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", host)
        if host != "files.mistral.ai" and not (
            azure_blob and parse_qs(parsed.query).get("sig")
        ):
            raise ImageDownloadError
        return httpx2.URL(value)
    except ValueError, httpx2.InvalidURL:
        raise ImageDownloadError from None


async def async_download_image_url(client: Mistral, url: str) -> httpx2.Response:
    """Stream approved storage without SDK auth, default headers or cookies."""
    request = httpx2.Request(
        "GET",
        _image_download_url(url),
        headers={"Accept-Encoding": "identity"},
        extensions={"timeout": httpx2.Timeout(REQUEST_TIMEOUT_MS / 1000).as_dict()},
    )
    # Every integration client uses HA's HTTPX2 transport or the account-owned
    # compatibility transport. A standalone Request avoids merging credentials
    # and cookies; auth=None also disables client-level authentication.
    transport = cast(httpx2.AsyncClient, client.sdk_configuration.async_client)
    try:
        return await transport.send(
            request, stream=True, auth=None, follow_redirects=False
        )
    except httpx2.TimeoutException:
        raise ImageDownloadError(ApiErrorKind.TIMEOUT) from None
    except httpx2.RequestError:
        raise ImageDownloadError(ApiErrorKind.CONNECTION) from None


async def async_download_image_file(client: Mistral, file_id: str) -> httpx2.Response:
    """Open a streaming response for one generated image file."""
    return await client.files.download_async(
        file_id=file_id,
        http_headers={"Accept-Encoding": "identity"},
        timeout_ms=REQUEST_TIMEOUT_MS,
    )


async def async_delete_generated_file(client: Mistral, file_id: str) -> bool:
    """Attempt provider deletion and report its explicit confirmation."""
    response = await client.files.delete_async(
        file_id=file_id, timeout_ms=SETUP_TIMEOUT_MS
    )
    return response.deleted and response.id == file_id


async def async_validate_api_key(
    hass: HomeAssistant, api_key: str
) -> list[MistralModel]:
    """Validate an API key and return available model metadata."""
    resources = await async_create_client(hass, api_key)
    try:
        return await async_get_models(resources.client)
    finally:
        await async_close_client(hass, resources)
