"""Provider error taxonomy.

Every provider maps native failures onto these types so the router and
agents never see vendor-specific exceptions.

Retry/failover semantics (fixed contract, enforced by the router):

- ``ProviderTimeout``, ``ProviderUnavailable``, ``ProviderRateLimited`` —
  transient: bounded retries on the same provider are allowed; failover is
  a consent-gated router decision only.
- ``ProviderAuthError`` — **no retry, no failover**; raised to the caller.
- ``ProviderInvalidRequest`` — **no retry, no failover**; raised to the
  caller.

``asyncio.CancelledError`` is NEVER mapped onto this taxonomy: providers
close their HTTP streams and re-raise it unchanged (see
``tests/unit/test_provider_contract.py``).
"""

from __future__ import annotations

# Bounded Retry-After policy (Plan v1.1, correction 4).
MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_RETRY_AFTER_SECONDS = 2.0
MIN_RETRY_AFTER_SECONDS = 1.0


class ProviderError(Exception):
    """Base class for all AI provider errors."""


class ProviderTimeout(ProviderError):
    """The provider did not answer in time."""


class ProviderUnavailable(ProviderError):
    """The provider is unreachable or disabled."""


class ProviderRateLimited(ProviderError):
    """The provider rejected the request due to rate limits.

    ``retry_after`` carries the provider-advised delay, already clamped to
    ``[MIN_RETRY_AFTER_SECONDS, MAX_RETRY_AFTER_SECONDS]`` by
    :func:`normalize_retry_after`.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = normalize_retry_after(retry_after)


class ProviderAuthError(ProviderError):
    """Authentication failed. Never retried, never fails over.

    Messages must never contain credential material.
    """


class ProviderInvalidRequest(ProviderError):
    """The request itself is invalid (bad model, malformed payload).

    Never retried, never fails over.
    """


class ProviderResponseError(ProviderError):
    """The provider returned an invalid or failed response."""


def normalize_retry_after(value: float | None) -> float:
    """Clamp an advised delay to the bounded policy window."""
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    if value < MIN_RETRY_AFTER_SECONDS:
        return MIN_RETRY_AFTER_SECONDS
    if value > MAX_RETRY_AFTER_SECONDS:
        return MAX_RETRY_AFTER_SECONDS
    return value
