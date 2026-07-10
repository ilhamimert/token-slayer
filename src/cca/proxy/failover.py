"""Multi-provider failover chain with exponential backoff.

When a provider fails (rate limit, timeout, outage), the chain
automatically retries the next provider without surfacing the error
to the caller.

Provider priority (default):
  1. anthropic  (primary)
  2. openai     (secondary)
  3. anthropic  (different model — last-resort fallback)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

# Errors that trigger a failover (as opposed to bugs the caller should handle)
_FAILOVER_ERRORS = (
    ConnectionError,
    TimeoutError,
    OSError,
)

try:
    import httpx
    _FAILOVER_ERRORS = (*_FAILOVER_ERRORS, httpx.HTTPStatusError, httpx.TimeoutException)  # type: ignore[assignment]
except ImportError:
    pass


@dataclass(frozen=True)
class ProviderEndpoint:
    name: str           # display name, e.g. "anthropic-haiku"
    provider: str       # "anthropic" | "openai"
    model: str          # model id
    base_url: str       # API base URL
    api_key_env: str    # environment variable that holds the API key


# Default provider chain — caller can supply a custom one
DEFAULT_CHAIN: list[ProviderEndpoint] = [
    ProviderEndpoint(
        name="anthropic-sonnet",
        provider="anthropic",
        model="claude-sonnet-4-6",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ProviderEndpoint(
        name="openai-gpt4o",
        provider="openai",
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    ),
    ProviderEndpoint(
        name="anthropic-haiku-fallback",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
    ),
]


class FailoverChain:
    """Execute a callable against each provider in order until one succeeds.

    Parameters
    ----------
    chain:
        Ordered list of ProviderEndpoint configurations to try.
    base_delay:
        Initial retry delay in seconds (doubles on each attempt).
    max_retries_per_provider:
        How many times to retry a single provider before moving to the next.
    """

    def __init__(
        self,
        chain: list[ProviderEndpoint] | None = None,
        base_delay: float = 1.0,
        max_retries_per_provider: int = 2,
    ) -> None:
        self._chain = chain or DEFAULT_CHAIN
        self._base_delay = base_delay
        self._max_retries = max_retries_per_provider

    def execute(
        self,
        fn: Callable[[ProviderEndpoint], str],
        *,
        on_failover: Callable[[ProviderEndpoint, Exception], None] | None = None,
    ) -> tuple[str, ProviderEndpoint]:
        """Call *fn(endpoint)* for each provider until success.

        Returns
        -------
        (response_text, endpoint_that_succeeded)

        Raises
        ------
        RuntimeError
            If all providers in the chain are exhausted.
        """
        last_exc: Exception | None = None
        for endpoint in self._chain:
            delay = self._base_delay
            for attempt in range(1, self._max_retries + 1):
                try:
                    result = fn(endpoint)
                    if attempt > 1 or endpoint != self._chain[0]:
                        logger.info(
                            "Failover succeeded: provider=%s model=%s attempt=%d",
                            endpoint.name, endpoint.model, attempt,
                        )
                    return result, endpoint
                except _FAILOVER_ERRORS as exc:
                    last_exc = exc
                    logger.warning(
                        "Provider %s failed (attempt %d/%d): %s",
                        endpoint.name, attempt, self._max_retries, exc,
                    )
                    if on_failover:
                        on_failover(endpoint, exc)
                    if attempt < self._max_retries:
                        time.sleep(delay)
                        delay *= 2  # exponential backoff

        raise RuntimeError(
            f"All providers exhausted. Last error: {last_exc}"
        ) from last_exc

    @property
    def providers(self) -> list[str]:
        return [ep.name for ep in self._chain]
