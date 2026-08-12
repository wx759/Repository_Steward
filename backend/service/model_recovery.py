from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ClassifiedError:
    reason: str
    retryable: bool
    status_code: int | None = None
    retry_after: float | None = None


def _status_code(exc: Exception) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
    return None


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, value)


def classify_model_error(exc: Exception) -> ClassifiedError:
    status = _status_code(exc)
    name = type(exc).__name__.lower()
    message = str(exc).lower()

    if (
        "context_length_exceeded" in message
        or "prompt_is_too_long" in message
        or "max_context_window" in message
        or ("prompt" in message and "too long" in message)
        or ("context" in message and "too long" in message)
    ):
        return ClassifiedError("prompt_too_long", False, status)
    if status == 429 or "ratelimit" in name or "rate limit" in message:
        return ClassifiedError("rate_limit", True, status, _retry_after(exc))
    if status == 529 or "overloaded" in name or "overloaded" in message:
        return ClassifiedError("overloaded", True, status, _retry_after(exc))
    if status in {500, 502, 503, 504}:
        return ClassifiedError("server_error", True, status, _retry_after(exc))
    if status in {401, 403} or "authentication" in name or "permissiondenied" in name:
        return ClassifiedError("authentication", False, status)
    if "timeout" in name or isinstance(exc, TimeoutError):
        return ClassifiedError("timeout", True, status)
    if "connection" in name:
        return ClassifiedError("connection", True, status)
    if status == 400:
        return ClassifiedError("bad_request", False, status)
    return ClassifiedError("unknown", False, status)


def retry_delay(
    retry_index: int,
    *,
    initial_delay: float,
    max_delay: float,
    backoff_factor: float = 2.0,
    jitter_ratio: float = 0.25,
    retry_after: float | None = None,
    random_uniform: Callable[[float, float], float] = random.uniform,
) -> float:
    if retry_after is not None:
        return retry_after
    base = min(initial_delay * (backoff_factor**retry_index), max_delay)
    return base + random_uniform(0.0, base * jitter_ratio)
