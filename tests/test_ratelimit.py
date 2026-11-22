"""Tests for client-side rate limiting."""

from ynab_tools.api.ratelimit import RateLimiter


def test_does_not_wait_below_the_limit():
    limiter = RateLimiter(limit=10, window=3600, reserve=0)
    slept = []

    for _ in range(9):
        limiter.acquire(sleep=slept.append)

    assert slept == []
    assert limiter.used == 9
    assert limiter.remaining == 1


def test_waits_once_the_limit_is_reached():
    limiter = RateLimiter(limit=3, window=3600, reserve=0)
    slept = []

    for _ in range(4):
        limiter.acquire(sleep=slept.append)

    assert len(slept) == 1
    # Waits out the remainder of the window for the oldest request.
    assert 3500 < slept[0] <= 3601


def test_reserve_keeps_headroom():
    """The reserve leaves room for an interactive command after a batch."""
    limiter = RateLimiter(limit=10, window=3600, reserve=4)

    assert limiter.remaining == 6


def test_old_requests_leave_the_window():
    limiter = RateLimiter(limit=3, window=3600, reserve=0)
    for _ in range(3):
        limiter.acquire(sleep=lambda _: None)
    assert limiter.used == 3

    # Age every recorded request past the window.
    limiter._times = type(limiter._times)(t - 4000 for t in limiter._times)

    assert limiter.used == 0
    assert limiter.remaining == 3
