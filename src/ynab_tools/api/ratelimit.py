"""Client-side rate limiting for the YNAB API.

YNAB allows 200 requests per access token per rolling hour and answers 429 once
that is exhausted. Without this, a bulk operation stops partway through and
leaves the budget half-changed — worse than not having started, because the
remainder has to be identified by hand before it can be retried.

Every request the SDK makes passes through ``ApiClient.call_api``, so throttling
there covers all of them regardless of which endpoint wrapper was called.
"""

import time
from collections import deque

import ynab

# YNAB's documented ceiling, and the window it applies over.
RATE_LIMIT = 200
WINDOW_SECONDS = 3600

# Stop short of the ceiling so an interactive command still has room to run
# after a long batch finishes.
DEFAULT_RESERVE = 10

MAX_RETRIES = 3


class RateLimiter:
    """Tracks request times in a rolling window and waits before exceeding it."""

    def __init__(self, limit: int = RATE_LIMIT, window: int = WINDOW_SECONDS,
                 reserve: int = DEFAULT_RESERVE):
        self.limit = limit
        self.window = window
        self.reserve = reserve
        self._times: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._times and now - self._times[0] >= self.window:
            self._times.popleft()

    @property
    def used(self) -> int:
        self._prune(time.monotonic())
        return len(self._times)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.reserve - self.used)

    def acquire(self, sleep=time.sleep) -> float:
        """Reserve a slot, waiting for the oldest request to age out if needed.

        Returns how long it waited, so callers can report an unexpected stall
        rather than appearing to hang.
        """
        now = time.monotonic()
        self._prune(now)
        waited = 0.0
        if len(self._times) >= self.limit - self.reserve:
            # Wait just past the point where the oldest request leaves the window.
            wait = self.window - (now - self._times[0]) + 1
            if wait > 0:
                sleep(wait)
                waited = wait
                now = time.monotonic()
                self._prune(now)
        self._times.append(now)
        return waited


_limiter = RateLimiter()


def get_limiter() -> RateLimiter:
    """The process-wide limiter, so every command shares one request budget."""
    return _limiter


def _is_rate_limited(exc: Exception) -> bool:
    return getattr(exc, "status", None) == 429


class RateLimitedApiClient(ynab.ApiClient):
    """An ApiClient that throttles requests and retries on 429.

    A 429 despite throttling means the token was used elsewhere — another
    session, or a run from before this process started — so back off and retry
    rather than failing the whole operation.
    """

    def call_api(self, method, url, header_params=None, body=None,
                 post_params=None, _request_timeout=None):
        for attempt in range(MAX_RETRIES):
            _limiter.acquire()
            try:
                return super().call_api(
                    method, url,
                    header_params=header_params,
                    body=body,
                    post_params=post_params,
                    _request_timeout=_request_timeout,
                )
            except Exception as e:
                if not _is_rate_limited(e) or attempt == MAX_RETRIES - 1:
                    raise
                # Exponential backoff: 60s, then 300s.
                time.sleep(60 * (5 ** attempt))
        raise RuntimeError("unreachable")
