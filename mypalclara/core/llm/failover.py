"""LLM provider failover with cooldown classification.

Provides failure classification, cooldown management, and a resilient provider
wrapper that retries transient errors and fails over to backup providers.

Inspired by OpenClaw's failover architecture.

Failure types:
    AUTH: 401/403, billing/payment issues -> cooldown entire provider (600s)
    RATE_LIMIT: 429, rate limit messages -> cooldown specific model (30s)
    CONTEXT_OVERFLOW: context/token limit -> rethrow immediately, never retry
    TRANSIENT: 5xx, timeout, connection -> retry with exponential backoff
    UNKNOWN: anything else -> treat as transient
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Default cooldown durations in seconds
AUTH_COOLDOWN = 600.0
RATE_LIMIT_COOLDOWN = 30.0


class FailoverReason(str, Enum):
    """Classification of LLM provider failures."""

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


# Patterns for error classification, checked in order.
# Each entry: (compiled regex, FailoverReason)
_ERROR_PATTERNS: list[tuple[re.Pattern[str], FailoverReason]] = [
    # Auth errors: HTTP 401, 403, billing, payment
    (
        re.compile(
            r"(?:401|403|unauthorized|forbidden|billing|payment)",
            re.IGNORECASE,
        ),
        FailoverReason.AUTH,
    ),
    # Rate limit: HTTP 429, rate limit text
    (
        re.compile(
            r"(?:429|too many requests|rate.?limit)",
            re.IGNORECASE,
        ),
        FailoverReason.RATE_LIMIT,
    ),
    # Context overflow: context length, token limit, too long
    (
        re.compile(
            r"(?:context.?length|token.?limit|maximum.?context|too.?long)",
            re.IGNORECASE,
        ),
        FailoverReason.CONTEXT_OVERFLOW,
    ),
    # Transient: 5xx errors, overloaded
    (
        re.compile(
            r"(?:50[0-4]|overloaded)",
            re.IGNORECASE,
        ),
        FailoverReason.TRANSIENT,
    ),
]

# Exception types that are always transient
_TRANSIENT_EXCEPTION_TYPES = (TimeoutError, ConnectionError, OSError)


def classify_error(error: BaseException) -> FailoverReason:
    """Classify an error into a FailoverReason.

    Uses regex pattern matching on the error message, plus exception type checks
    for timeout/connection errors.

    Args:
        error: The exception to classify.

    Returns:
        The classified FailoverReason.
    """
    # Check exception type first for timeouts and connection errors
    if isinstance(error, _TRANSIENT_EXCEPTION_TYPES):
        return FailoverReason.TRANSIENT

    # Check error message against patterns
    message = str(error)
    for pattern, reason in _ERROR_PATTERNS:
        if pattern.search(message):
            return reason

    return FailoverReason.UNKNOWN


class CooldownManager:
    """Tracks provider and model cooldowns with expiry timestamps.

    Uses time.monotonic() for reliable elapsed time measurement.
    Supports both provider-level cooldowns (e.g., auth failure) and
    model-level cooldowns (e.g., rate limiting on a specific model).
    """

    def __init__(self) -> None:
        # Key: (provider_name, model_name | None)
        # Value: (expiry_monotonic, reason)
        self._cooldowns: dict[tuple[str, str | None], tuple[float, FailoverReason]] = {}

    def set_cooldown(
        self,
        provider: str,
        model: str | None,
        duration: float,
        reason: FailoverReason,
    ) -> None:
        """Set a cooldown for a provider or provider+model.

        Args:
            provider: Provider name (e.g., "openrouter").
            model: Model name, or None for provider-level cooldown.
            duration: Cooldown duration in seconds.
            reason: The failure reason that triggered this cooldown.
        """
        expiry = time.monotonic() + duration
        key = (provider, model)
        self._cooldowns[key] = (expiry, reason)
        if model is None:
            logger.warning(
                "Provider %s cooled down for %.0fs (reason: %s)",
                provider,
                duration,
                reason.value,
            )
        else:
            logger.warning(
                "Model %s/%s cooled down for %.0fs (reason: %s)",
                provider,
                model,
                duration,
                reason.value,
            )

    def is_cooled_down(self, provider: str, model: str | None = None) -> bool:
        """Check if a provider or model is currently in cooldown.

        A provider-level cooldown blocks all models under that provider.
        A model-level cooldown only blocks that specific model.

        Args:
            provider: Provider name.
            model: Model name, or None to check provider-level only.

        Returns:
            True if the provider/model is in cooldown and should be skipped.
        """
        now = time.monotonic()

        # Check provider-level cooldown (blocks everything)
        provider_key = (provider, None)
        if provider_key in self._cooldowns:
            expiry, _reason = self._cooldowns[provider_key]
            if now < expiry:
                return True
            # Expired, clean up
            del self._cooldowns[provider_key]

        # Check model-level cooldown (only if model specified)
        if model is not None:
            model_key = (provider, model)
            if model_key in self._cooldowns:
                expiry, _reason = self._cooldowns[model_key]
                if now < expiry:
                    return True
                # Expired, clean up
                del self._cooldowns[model_key]

        return False

    def clear(self, provider: str, model: str | None = None) -> None:
        """Clear cooldown for a provider or specific model.

        Args:
            provider: Provider name.
            model: Model name, or None to clear provider-level cooldown.
        """
        key = (provider, model)
        self._cooldowns.pop(key, None)
        if model is None:
            # Also clear all model-specific cooldowns for this provider
            to_remove = [k for k in self._cooldowns if k[0] == provider]
            for k in to_remove:
                del self._cooldowns[k]


class ResilientProvider:
    """Wraps a primary provider with fallbacks, retry, and cooldown management.

    Provides the same interface as the wrapped providers (complete,
    complete_with_tools, stream, stream_with_tools) but adds:
    - Automatic retry with exponential backoff for transient errors
    - Failover to backup providers on auth/rate-limit errors
    - Cooldown tracking to avoid repeatedly hitting failing providers
    - Immediate rethrow for context overflow (never retried)

    Args:
        primary: The primary provider to use.
        fallbacks: List of fallback providers, tried in order.
        cooldowns: CooldownManager instance for tracking cooldowns.
        max_retries: Maximum retry attempts for transient errors (default: 3).
        base_delay: Base delay in seconds for exponential backoff (default: 1.0).
    """

    def __init__(
        self,
        primary: Any,
        fallbacks: list[Any],
        cooldowns: CooldownManager,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        self._primary = primary
        self._fallbacks = fallbacks
        self._cooldowns = cooldowns
        self._max_retries = max_retries
        self._base_delay = base_delay

    @property
    def provider_name(self) -> str:
        """The primary provider's name."""
        return self._primary.provider_name

    @property
    def model_name(self) -> str:
        """The primary provider's model name."""
        return self._primary.model_name

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Complete with failover support."""
        return await self._call_with_failover("complete", messages, **kwargs)

    async def complete_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Complete with tools and failover support."""
        return await self._call_with_failover("complete_with_tools", messages, **kwargs)

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Stream with failover support."""
        return await self._call_with_failover("stream", messages, **kwargs)

    async def stream_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Stream with tools and failover support."""
        return await self._call_with_failover("stream_with_tools", messages, **kwargs)

    async def _call_with_failover(self, method: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Try providers in order with retry and failover logic.

        For each provider:
        1. Skip if currently in cooldown
        2. Attempt the call
        3. On CONTEXT_OVERFLOW: rethrow immediately (never retry/failover)
        4. On AUTH: cooldown entire provider (600s), move to next provider
        5. On RATE_LIMIT: cooldown specific model (30s), move to next provider
        6. On TRANSIENT/UNKNOWN: retry with exponential backoff, then failover

        Args:
            method: Method name to call on the provider (e.g., "complete").
            messages: The messages to pass to the provider.
            **kwargs: Additional keyword arguments for the method.

        Returns:
            The provider's response.

        Raises:
            Exception: If all providers fail, re-raises the last error.
        """
        providers = [self._primary, *self._fallbacks]
        last_error: BaseException | None = None

        for provider in providers:
            provider_name = provider.provider_name
            model_name = provider.model_name

            # Skip providers/models in cooldown
            if self._cooldowns.is_cooled_down(provider_name, model_name):
                logger.info("Skipping %s/%s (in cooldown)", provider_name, model_name)
                continue

            # Attempt with retries for transient errors
            last_error = await self._try_provider(provider, provider_name, model_name, method, messages, **kwargs)

            if last_error is None:
                # Success is stored in self._last_result by _try_provider
                return self._last_result

        # All providers failed
        if last_error is not None:
            raise last_error
        raise RuntimeError("No providers available (all in cooldown)")

    async def _try_provider(
        self,
        provider: Any,
        provider_name: str,
        model_name: str,
        method: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> BaseException | None:
        """Try a single provider with retries for transient errors.

        Returns None on success (result stored in self._last_result),
        or the last error on failure.
        """
        fn = getattr(provider, method)

        for attempt in range(self._max_retries):
            try:
                self._last_result = await fn(messages, **kwargs)
                return None  # Success
            except Exception as e:
                reason = classify_error(e)

                # Context overflow: never retry, never failover
                if reason == FailoverReason.CONTEXT_OVERFLOW:
                    logger.error(
                        "Context overflow on %s/%s, rethrowing: %s",
                        provider_name,
                        model_name,
                        e,
                    )
                    raise

                # Auth error: cooldown entire provider, move to next
                if reason == FailoverReason.AUTH:
                    logger.warning(
                        "Auth error on %s/%s: %s",
                        provider_name,
                        model_name,
                        e,
                    )
                    self._cooldowns.set_cooldown(provider_name, None, AUTH_COOLDOWN, reason)
                    return e

                # Rate limit: cooldown specific model, move to next
                if reason == FailoverReason.RATE_LIMIT:
                    logger.warning(
                        "Rate limit on %s/%s: %s",
                        provider_name,
                        model_name,
                        e,
                    )
                    self._cooldowns.set_cooldown(provider_name, model_name, RATE_LIMIT_COOLDOWN, reason)
                    return e

                # Transient/Unknown: retry with exponential backoff + jitter
                last_attempt = attempt == self._max_retries - 1
                if last_attempt:
                    logger.warning(
                        "Transient error on %s/%s after %d retries: %s",
                        provider_name,
                        model_name,
                        self._max_retries,
                        e,
                    )
                    return e

                # Exponential backoff: base_delay * 2^attempt, with jitter
                import random

                delay = self._base_delay * (2**attempt)
                jitter = random.uniform(0, delay * 0.1)  # noqa: S311
                delay += jitter
                logger.info(
                    "Transient error on %s/%s (attempt %d/%d), retrying in %.2fs: %s",
                    provider_name,
                    model_name,
                    attempt + 1,
                    self._max_retries,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)

        # Should not reach here, but just in case
        return RuntimeError(f"Exhausted retries for {provider_name}/{model_name}")
