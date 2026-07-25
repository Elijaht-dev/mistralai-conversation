"""Tests for Mistral SDK error normalization."""

from __future__ import annotations

import httpx
import pytest
from mistralai.client.errors import NoResponseError

from custom_components.mistral_conversation.const import ApiErrorKind
from custom_components.mistral_conversation.errors import (
    api_error_message,
    classify_api_error,
    is_auth_error,
)

from .helpers import mistral_error


@pytest.mark.parametrize("status_code", [401, 403])
def test_authentication_errors(status_code: int) -> None:
    """Rejected credentials have a dedicated category."""
    error = mistral_error(status_code)

    assert classify_api_error(error) is ApiErrorKind.AUTHENTICATION
    assert is_auth_error(error)


def test_rate_limit_error() -> None:
    """HTTP 429 is distinguished from other provider failures."""
    assert classify_api_error(mistral_error(429)) is ApiErrorKind.RATE_LIMIT


@pytest.mark.parametrize("status_code", [408, 504])
def test_provider_timeout_errors(status_code: int) -> None:
    """Provider timeout statuses map to the timeout category."""
    assert classify_api_error(mistral_error(status_code)) is ApiErrorKind.TIMEOUT


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_provider_server_errors(status_code: int) -> None:
    """Server errors mark the provider connection unavailable."""
    assert classify_api_error(mistral_error(status_code)) is ApiErrorKind.CONNECTION


def test_request_and_timeout_errors() -> None:
    """HTTP transport failures use connection-aware categories."""
    request = httpx.Request("GET", "https://api.mistral.ai")

    assert (
        classify_api_error(httpx.ConnectError("offline", request=request))
        is ApiErrorKind.CONNECTION
    )
    assert (
        classify_api_error(httpx.ReadTimeout("slow", request=request))
        is ApiErrorKind.TIMEOUT
    )
    assert classify_api_error(TimeoutError("slow")) is ApiErrorKind.TIMEOUT
    assert classify_api_error(NoResponseError("no response")) is ApiErrorKind.CONNECTION


def test_other_api_error() -> None:
    """Validation and unclassified errors remain regular API errors."""
    error = mistral_error(400)

    assert classify_api_error(error) is ApiErrorKind.API
    assert not is_auth_error(error)
    assert classify_api_error(ValueError("bad")) is ApiErrorKind.API


def test_error_message_is_single_line_and_bounded() -> None:
    """Provider details cannot create unbounded or multiline UI errors."""
    message = f"  first\nsecond  {'x' * 500}"

    normalized = api_error_message(mistral_error(400, message))

    assert "\n" not in normalized
    assert len(normalized) == 400
    assert normalized.endswith("…")


def test_empty_error_message_falls_back_to_type() -> None:
    """A useful exception class is shown when the message is blank."""
    assert api_error_message(ValueError("")) == "ValueError"
