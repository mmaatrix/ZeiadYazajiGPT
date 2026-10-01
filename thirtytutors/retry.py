"""
Retry helper for OpenAI API calls used by background summarization and other non-Realtime requests.

OpenAI API calls can fail transiently because of rate limits, server errors, timeouts, or network failures. This single-user local app retries those cases with bounded exponential backoff.
"""

import logging
import re
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 4  # additional attempts after the first, so 5 tries total
DEFAULT_BASE_BACKOFF = 2.0  # seconds, doubles each attempt


def is_transient_error(e: Exception) -> bool:
    """Best-effort classification of 'worth retrying' vs 'will never work'.

    OpenAI SDK server/rate-limit/connection/timeout exceptions are transient.
    For generic exception wrappers, also recognize common retryable HTTP status
    codes in the message while leaving 400/401/404 errors non-retryable.
    """
    if type(e).__name__ in {
        "RateLimitError",
        "InternalServerError",
        "ServerError",
        "APIConnectionError",
        "APITimeoutError",
    }:
        return True
    text = str(e).upper().strip()
    return (
        text.startswith(("429", "500", "502", "503", "504"))
        or "INTERNAL ERROR" in text
    )


def is_network_error(e: Exception) -> bool:
    """True for failures that happened before any request reached OpenAI."""
    return isinstance(e, OSError) or type(e).__name__ in {"APIConnectionError", "APITimeoutError"}


def is_rate_limit_error(e: Exception) -> bool:
    """True specifically for a 429/rate-limit quota error."""
    text = str(e).upper()
    return type(e).__name__ == "RateLimitError" or "429" in text or "RATE LIMIT" in text


def parse_retry_delay(text: str) -> float | None:
    """Extract a server-suggested retry delay and add a 1s safety margin."""
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
    return float(match.group(1)) + 1.0 if match else None


def call_with_retry(
    fn: Callable[..., T],
    *args,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_backoff: float = DEFAULT_BASE_BACKOFF,
    label: str = "call",
    **kwargs,
) -> T:
    """Call fn and retry transient failures with bounded exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if not is_transient_error(e) or attempt == max_retries:
                raise
            delay = parse_retry_delay(str(e)) or (base_backoff * (2**attempt))
            logger.warning(
                "[%s] attempt %d/%d failed (%s) - retrying in %.1fs",
                label,
                attempt + 1,
                max_retries + 1,
                type(e).__name__,
                delay,
            )
            time.sleep(delay)
    raise last_exc  # unreachable, keeps type checkers happy
