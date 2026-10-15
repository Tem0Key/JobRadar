"""Collector contract and bounded HTTP retries shared by all source adapters."""

import math
import random as random_module
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from jobradar.config import HTTPSettings, SourceConfig
from jobradar.models import Job


class CollectionError(Exception):
    """A public source could not be retrieved or decoded safely."""


@dataclass
class CollectorResult:
    jobs: list[Job] = field(default_factory=list)
    complete: bool = True
    errors: list[str] = field(default_factory=list)


class Collector(ABC):
    def __init__(self, http: "RetryingHTTPClient") -> None:
        self.http = http

    @abstractmethod
    def collect(self, source: SourceConfig) -> CollectorResult:
        """Return valid jobs and explicitly indicate an incomplete snapshot."""


class RetryingHTTPClient:
    """Synchronous client with one request-rate gate, including retry attempts.

    An injected client is owned by its caller. This wrapper owns and closes only
    clients it creates. Server-supplied Retry-After is honored; delays exceeding
    ``max_backoff`` defer the board to a later scheduled run without another request.
    """

    def __init__(
        self,
        settings: HTTPSettings | None = None,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        random: Callable[[], float] = random_module.random,
    ) -> None:
        self.settings = settings or HTTPSettings()
        self._owns_client = client is None
        self.client = client or httpx.Client(
            headers={"User-Agent": "JobRadar/0.1 (personal public job board collector)"},
            follow_redirects=False,
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._random = random
        self._last_request: float | None = None
        self._rate_lock = threading.Lock()

    def _request(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        with self._rate_lock:
            if self._last_request is not None:
                remaining = self.settings.min_interval - (self._monotonic() - self._last_request)
                if remaining > 0:
                    self._sleep(remaining)
            self._last_request = self._monotonic()
        return self.client.get(url, params=params, timeout=self.settings.timeout)

    def _retry_after(self, response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                deadline = parsedate_to_datetime(value)
                if deadline.tzinfo is None:
                    return None
                seconds = (deadline - datetime.now(UTC)).total_seconds()
            except (ValueError, OverflowError, TypeError):
                return None
        if not math.isfinite(seconds):
            return None
        if seconds > self.settings.max_backoff:
            raise CollectionError(
                f"Retry-After requests a wait of {seconds:.0f}s, exceeding max_backoff "
                f"({self.settings.max_backoff:g}s); board deferred until the next scheduled run"
            )
        return max(0.0, seconds)

    def _pause(self, attempt: int, retry_after: float | None) -> None:
        backoff = self.settings.backoff_base * 2**attempt
        jitter = backoff * 0.25 * max(0.0, min(1.0, self._random()))
        delay = min(self.settings.max_backoff, backoff + jitter)
        if retry_after is not None:
            delay = max(delay, retry_after)
        if delay > 0:
            self._sleep(delay)

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.settings.attempts):
            response = None
            retry_after = None
            try:
                response = self._request(url, params)
            except httpx.RequestError as exc:
                if attempt + 1 == self.settings.attempts:
                    raise CollectionError(
                        f"Request failed after {self.settings.attempts} attempts: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
            else:
                retryable = response.status_code == 429 or 500 <= response.status_code < 600
                if not retryable:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise CollectionError(f"HTTP {response.status_code} from {url}") from exc
                    try:
                        return response.json()
                    except (ValueError, UnicodeError) as exc:
                        raise CollectionError(f"Invalid JSON from {url}") from exc
                retry_after = self._retry_after(response)
                if attempt + 1 == self.settings.attempts:
                    raise CollectionError(
                        f"HTTP {response.status_code} after {self.settings.attempts} attempts"
                    )
            self._pause(attempt, retry_after)
        raise AssertionError("unreachable: attempts are validated as positive")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "RetryingHTTPClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
