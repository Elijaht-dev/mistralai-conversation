"""Tests for the typed Mistral SDK boundary."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx2
import pytest
from homeassistant.core import HomeAssistant
from mistralai.client.models import (
    AudioSpeechCapabilities,
    BaseModelCard,
    FTModelCard,
    ModelCapabilities,
)

from custom_components.mistral_conversation.api import (
    MistralClientResources,
    async_close_client,
    async_create_client,
    async_get_models,
    async_get_voices,
    async_validate_api_key,
)
from custom_components.mistral_conversation.const import (
    SETUP_TIMEOUT_MS,
    VOICE_LIST_PAGE_SIZE,
)


def _model_card(
    model_id: str,
    *,
    chat: bool = True,
    function_calling: bool = False,
    reasoning: bool = False,
    vision: bool = False,
    ocr: bool = False,
) -> BaseModelCard:
    """Build a provider model card."""
    return BaseModelCard(
        id=model_id,
        capabilities=ModelCapabilities(
            completion_chat=chat,
            function_calling=function_calling,
            reasoning=reasoning,
            vision=vision,
            ocr=ocr,
        ),
    )


async def test_get_models_parses_metadata_and_sorts() -> None:
    """Model cards are filtered and converted into stable metadata."""
    deprecation = datetime(2027, 1, 2, tzinfo=UTC)
    rich_model = BaseModelCard(
        id="z-model",
        name="Zed",
        description="A capable model",
        aliases=["z-latest"],
        max_context_length=131072,
        default_model_temperature=0.35,
        deprecation=deprecation,
        deprecation_replacement_model="next-model",
        capabilities=ModelCapabilities(
            completion_chat=True,
            function_calling=True,
            reasoning=True,
            vision=True,
            ocr=True,
        ),
    )
    client = MagicMock()
    client.models.list_async = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                rich_model,
                _model_card("not-chat", chat=False),
                _model_card("a-model"),
                object(),
            ]
        )
    )

    models = await async_get_models(client)

    assert [model.id for model in models] == ["a-model", "not-chat", "z-model"]
    assert not models[1].completion_chat
    model = models[2]
    assert model.name == "Zed"
    assert model.description == "A capable model"
    assert model.aliases == ("z-latest",)
    assert model.max_context_length == 131072
    assert model.default_temperature == 0.35
    assert model.deprecation == deprecation
    assert model.replacement_model == "next-model"
    assert model.function_calling
    assert model.reasoning
    assert model.vision
    assert model.ocr
    client.models.list_async.assert_awaited_once_with(timeout_ms=SETUP_TIMEOUT_MS)


async def test_get_models_filters_archived_fine_tunes() -> None:
    """Archived fine-tuned models are not offered to new agents."""
    client = MagicMock()
    client.models.list_async = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                FTModelCard(
                    id="archived",
                    job="job-id",
                    root="root-model",
                    archived=True,
                    capabilities=ModelCapabilities(completion_chat=True),
                ),
                FTModelCard(
                    id="active",
                    job="job-id",
                    root="root-model",
                    archived=False,
                    capabilities=ModelCapabilities(completion_chat=True),
                ),
            ]
        )
    )

    models = await async_get_models(client)

    assert [model.id for model in models] == ["active"]


async def test_get_voices_pages_filters_and_sorts() -> None:
    """Preset and custom voices are returned as stable account metadata."""
    client = MagicMock()
    client.audio.voices.search_async = AsyncMock(
        side_effect=[
            SimpleNamespace(
                data=[
                    SimpleNamespace(id="voice-z", name="Zulu", languages=["en"]),
                    SimpleNamespace(id="", name="Ignored", languages=[]),
                ],
                next_page_token="second-page",
            ),
            SimpleNamespace(
                data=[
                    SimpleNamespace(
                        id="voice-a",
                        name="Alpha",
                        languages=["fr", "en"],
                    )
                ],
                next_page_token=None,
            ),
        ]
    )

    pages = client.audio.voices.search_async.side_effect
    client.audio.voices.search_async.side_effect = [
        SimpleNamespace(result=page) for page in pages
    ]
    voices = await async_get_voices(client)

    assert [voice.id for voice in voices] == ["voice-a", "voice-z"]
    assert voices[0].languages == ("fr", "en")
    assert client.audio.voices.search_async.await_args_list[0].kwargs == {
        "page_size": VOICE_LIST_PAGE_SIZE,
        "page_token": None,
        "type_": "all",
        "timeout_ms": SETUP_TIMEOUT_MS,
    }
    assert (
        client.audio.voices.search_async.await_args_list[1].kwargs["page_token"]
        == "second-page"
    )


async def test_get_voices_stops_on_empty_page() -> None:
    """An empty provider page terminates pagination without retries."""
    client = MagicMock()
    client.audio.voices.search_async = AsyncMock(
        return_value=SimpleNamespace(
            result=SimpleNamespace(data=[], next_page_token="unused")
        )
    )

    assert await async_get_voices(client) == []
    client.audio.voices.search_async.assert_awaited_once()


async def test_create_client_uses_home_assistant_http_client(
    hass: HomeAssistant,
) -> None:
    """The SDK shares Home Assistant's managed asynchronous transport."""
    shared_client = MagicMock(spec=httpx2.AsyncClient)
    sdk_client = MagicMock()
    loop_thread = threading.get_ident()

    def shared_transport(hass: HomeAssistant) -> object:
        assert threading.get_ident() == loop_thread
        return shared_client

    def construct(**kwargs: object) -> MagicMock:
        assert threading.get_ident() != loop_thread
        return sdk_client

    with (
        patch(
            "custom_components.mistral_conversation.api.get_async_client",
            side_effect=shared_transport,
        ),
        patch(
            "custom_components.mistral_conversation.api.Mistral",
            side_effect=construct,
        ) as mistral,
    ):
        result = await async_create_client(hass, "test-api-key")

    assert result.client is sdk_client
    assert result.owned_http_client is None
    mistral.assert_called_once_with(
        api_key="test-api-key",
        async_client=shared_client,
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("cleanup_failure", [False, True])
async def test_create_client_closes_on_service_initialization_failure(
    hass: HomeAssistant,
    legacy: bool,
    cleanup_failure: bool,
) -> None:
    """A failed lazy service initialization releases SDK-owned resources."""
    client = MagicMock()
    loop_thread = threading.get_ident()

    def close(*args: object) -> None:
        assert threading.get_ident() != loop_thread

    client.__exit__.side_effect = close
    transport = MagicMock(spec=httpx2.AsyncClient)
    if cleanup_failure:
        transport.aclose.side_effect = RuntimeError("cleanup failure")
    with (
        patch(
            "custom_components.mistral_conversation.api.get_async_client",
            return_value=object() if legacy else transport,
        ),
        patch(
            "custom_components.mistral_conversation.api._create_http_client",
            return_value=transport,
        ),
        patch(
            "custom_components.mistral_conversation.api.Mistral", return_value=client
        ),
        patch.object(
            type(client), "audio", new_callable=PropertyMock, create=True
        ) as audio,
        pytest.raises(RuntimeError, match="service initialization"),
    ):
        audio.side_effect = RuntimeError("service initialization")
        await async_create_client(hass, "test-api-key")
    client.__exit__.assert_called_once_with(None, None, None)
    assert transport.aclose.await_count == int(legacy)


@pytest.mark.parametrize("legacy", [False, True])
async def test_create_client_closes_after_cancellation(
    hass: HomeAssistant, legacy: bool
) -> None:
    """Cancellation during worker construction cannot leak the resulting client."""
    started = asyncio.Event()
    release = threading.Event()
    client = MagicMock()
    client.__aexit__ = AsyncMock()
    loop = asyncio.get_running_loop()

    def construct(**kwargs: object) -> MagicMock:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=10)
        return client

    transport = MagicMock(spec=httpx2.AsyncClient)
    with (
        patch(
            "custom_components.mistral_conversation.api.get_async_client",
            return_value=object() if legacy else transport,
        ),
        patch(
            "custom_components.mistral_conversation.api._create_http_client",
            return_value=transport,
        ),
        patch(
            "custom_components.mistral_conversation.api.Mistral", side_effect=construct
        ),
    ):
        task = asyncio.create_task(async_create_client(hass, "test-api-key"))
        try:
            await asyncio.wait_for(started.wait(), timeout=10)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    client.__exit__.assert_called_once_with(None, None, None)
    client.__aexit__.assert_awaited_once_with(None, None, None)
    assert transport.aclose.await_count == int(legacy)


async def test_legacy_client_uses_verified_owned_httpx2_transport(hass):
    """The real compatibility transport is verified and closed deterministically."""
    with patch(
        "custom_components.mistral_conversation.api.get_async_client",
        return_value=object(),
    ):
        resources = await async_create_client(hass, "test-api-key")
    assert isinstance(resources.owned_http_client, httpx2.AsyncClient)
    assert (
        resources.client.sdk_configuration.async_client is resources.owned_http_client
    )
    assert not resources.owned_http_client.is_closed
    await async_close_client(hass, resources)
    assert resources.owned_http_client.is_closed


async def test_get_voices_deduplicates_and_bounds_repeated_cursors():
    """A looping provider cursor cannot cause unbounded requests or duplicate voices."""
    client = MagicMock()
    voice = SimpleNamespace(id="voice", name="Preset", languages=[])
    client.audio.voices.search_async = AsyncMock(
        return_value=SimpleNamespace(
            result=SimpleNamespace(data=[voice, voice], next_page_token="same")
        )
    )
    voices = await async_get_voices(client)
    assert [voice.id for voice in voices] == ["voice"]
    assert client.audio.voices.search_async.await_count == 2


async def test_get_voices_preserves_provider_count_bound(monkeypatch):
    """Invalid or duplicate rows still consume the pagination budget."""
    monkeypatch.setattr("custom_components.mistral_conversation.api.MAX_VOICES", 3)
    client = MagicMock()
    voice = SimpleNamespace(id="voice", name="Preset", languages=[])
    client.audio.voices.search_async = AsyncMock(
        side_effect=[
            SimpleNamespace(
                result=SimpleNamespace(data=[voice, voice], next_page_token="next")
            ),
            SimpleNamespace(
                result=SimpleNamespace(
                    data=[SimpleNamespace(id="other", name="Other", languages=[])],
                    next_page_token="again",
                )
            ),
        ]
    )
    assert len(await async_get_voices(client)) == 2
    assert client.audio.voices.search_async.await_args_list[1].kwargs["page_size"] == 1


async def test_audio_model_discovery_retains_capabilities():
    client = MagicMock()
    client.models.list_async = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                BaseModelCard(
                    id="voice",
                    aliases=["voice-latest"],
                    audio_speech_capabilities=AudioSpeechCapabilities(
                        modes=["offline", "realtime"], response_formats=["mp3", "wav"]
                    ),
                    capabilities=ModelCapabilities(
                        completion_chat=False,
                        audio_transcription=True,
                        audio_transcription_realtime=True,
                        audio_speech=True,
                    ),
                )
            ]
        )
    )
    (model,) = await async_get_models(client)
    assert not model.completion_chat
    assert (
        model.audio_transcription
        and model.audio_transcription_realtime
        and model.audio_speech
    )
    assert model.matches("voice-latest")
    assert model.audio_speech_modes == ("offline", "realtime")
    assert model.audio_speech_formats == ("mp3", "wav")


async def test_validate_api_key_closes_client(
    hass: HomeAssistant,
) -> None:
    """Temporary validation clients are closed after a successful request."""
    client = MagicMock()
    models = []
    with (
        patch(
            "custom_components.mistral_conversation.api.async_create_client",
            return_value=MistralClientResources(client),
        ),
        patch(
            "custom_components.mistral_conversation.api.async_get_models",
            new_callable=AsyncMock,
            return_value=models,
        ),
        patch(
            "custom_components.mistral_conversation.api.async_close_client",
            new_callable=AsyncMock,
        ) as close_client,
    ):
        result = await async_validate_api_key(hass, "secret")

    assert result is models
    close_client.assert_awaited_once_with(hass, MistralClientResources(client))


async def test_validate_api_key_closes_client_on_error(
    hass: HomeAssistant,
) -> None:
    """Temporary validation clients are also closed after provider errors."""
    client = MagicMock()
    with (
        patch(
            "custom_components.mistral_conversation.api.async_create_client",
            return_value=MistralClientResources(client),
        ),
        patch(
            "custom_components.mistral_conversation.api.async_get_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "custom_components.mistral_conversation.api.async_close_client",
            new_callable=AsyncMock,
        ) as close_client,
        pytest.raises(RuntimeError, match="boom"),
    ):
        await async_validate_api_key(hass, "secret")

    close_client.assert_awaited_once_with(hass, MistralClientResources(client))


async def test_close_client_suppresses_cleanup_errors(hass: HomeAssistant) -> None:
    """Cleanup cannot mask the original setup or unload failure."""
    client = MagicMock()
    client.__exit__.side_effect = RuntimeError("sync close")
    client.__aexit__ = AsyncMock(side_effect=RuntimeError("async close"))

    await async_close_client(hass, MistralClientResources(client))

    client.__exit__.assert_called_once_with(None, None, None)
    client.__aexit__.assert_awaited_once_with(None, None, None)
