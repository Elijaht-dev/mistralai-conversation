"""Error normalization for the Mistral AI Conversation integration."""

from __future__ import annotations

import re

import httpx
from mistralai.client.errors import MistralError, NoResponseError

from .const import ApiErrorKind

_MAX_ERROR_MESSAGE_LENGTH = 400
_WHITESPACE = re.compile(r"\s+")


def classify_api_error(err: BaseException) -> ApiErrorKind:
    """Classify an exception raised while communicating with Mistral."""
    error_kind = ApiErrorKind.API
    if isinstance(err, MistralError):
        if err.status_code in (401, 403):
            error_kind = ApiErrorKind.AUTHENTICATION
        elif err.status_code == 429:
            error_kind = ApiErrorKind.RATE_LIMIT
        elif err.status_code in (408, 504):
            error_kind = ApiErrorKind.TIMEOUT
        elif err.status_code is not None and err.status_code >= 500:
            error_kind = ApiErrorKind.CONNECTION
    elif isinstance(err, TimeoutError | httpx.TimeoutException):
        error_kind = ApiErrorKind.TIMEOUT
    elif isinstance(err, NoResponseError | httpx.RequestError):
        error_kind = ApiErrorKind.CONNECTION
    return error_kind


def api_error_message(err: BaseException) -> str:
    """Return a bounded, single-line provider error message."""
    if isinstance(err, MistralError | NoResponseError):
        message = err.message
    else:
        message = str(err)

    normalized = _WHITESPACE.sub(" ", message).strip()
    if not normalized:
        return type(err).__name__
    if len(normalized) <= _MAX_ERROR_MESSAGE_LENGTH:
        return normalized
    return f"{normalized[: _MAX_ERROR_MESSAGE_LENGTH - 1]}…"


def is_auth_error(err: BaseException) -> bool:
    """Return whether an error represents rejected credentials."""
    return classify_api_error(err) is ApiErrorKind.AUTHENTICATION
