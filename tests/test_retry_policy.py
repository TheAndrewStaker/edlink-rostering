"""Verify the canonical retry-policy classifications.

The policy lives in :mod:`edlink_rostering.core.retry`. These tests are
deliberately classification-only: they confirm the policy correctly
identifies retryable vs non-retryable conditions, without actually
spinning up a tenacity retry loop. The loop-driving tests will land
with the production EdLink HTTP client.
"""

from __future__ import annotations

import httpx
import pytest

from edlink_rostering.core.retry import (
    RETRY_CONDITION,
    RETRYABLE_STATUSES,
    _is_retryable_response,
)


def _request() -> httpx.Request:
    return httpx.Request("GET", "https://example.test/")


def test_retryable_statuses_cover_429_and_5xx() -> None:
    assert 429 in RETRYABLE_STATUSES
    assert 500 in RETRYABLE_STATUSES
    assert 502 in RETRYABLE_STATUSES
    assert 503 in RETRYABLE_STATUSES
    assert 504 in RETRYABLE_STATUSES


def test_non_retryable_statuses_excluded() -> None:
    for code in (200, 201, 204, 301, 400, 401, 403, 404, 409, 422):
        assert code not in RETRYABLE_STATUSES


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_response_with_retryable_status_is_retried(code: int) -> None:
    response = httpx.Response(code, request=_request())
    assert _is_retryable_response(response) is True


@pytest.mark.parametrize("code", [200, 400, 401, 403, 404, 422])
def test_response_with_non_retryable_status_is_not_retried(code: int) -> None:
    response = httpx.Response(code, request=_request())
    assert _is_retryable_response(response) is False


def test_non_response_object_is_not_retried() -> None:
    """Tenacity may pass through any value; only Response objects qualify."""

    assert _is_retryable_response(None) is False
    assert _is_retryable_response({"status_code": 500}) is False
    assert _is_retryable_response("error") is False


def test_retry_condition_fires_on_transport_error() -> None:
    """httpx.TransportError (connection reset, DNS, TLS) is retried."""

    from tenacity import RetryCallState

    # Build a minimal RetryCallState with an exception outcome.
    # Tenacity's retry_if_exception_type checks outcome.exception().
    class _FakeOutcome:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc

        def failed(self) -> bool:
            return True

        def exception(self) -> BaseException:
            return self._exc

        def result(self) -> object:
            raise self._exc

    state = RetryCallState.__new__(RetryCallState)
    state.outcome = _FakeOutcome(httpx.ConnectError("reset"))  # type: ignore[assignment]
    assert RETRY_CONDITION(state) is True


def test_retry_condition_does_not_fire_on_application_error() -> None:
    """Non-transient exceptions (bugs) are not retried."""

    from tenacity import RetryCallState

    class _FakeOutcome:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc

        def failed(self) -> bool:
            return True

        def exception(self) -> BaseException:
            return self._exc

        def result(self) -> object:
            raise self._exc

    state = RetryCallState.__new__(RetryCallState)
    state.outcome = _FakeOutcome(ValueError("not a transient failure"))  # type: ignore[assignment]
    assert RETRY_CONDITION(state) is False
