"""The throttle's check-and-reserve step (#151, group B review).

``begin()`` lets a password check through and counts it at once, so the
requests that arrive while its scrypt runs are held back; ``failed()``,
``succeeded()`` and ``cancel()`` settle it. The login and Settings routes use
it; tests/integration covers them with a real burst.
"""

from src.api.auth.throttle import CLIENT_CEILING, FREE_FAILURES, LoginThrottle
from tests.auth_support import FrozenClock

CLIENT = "198.51.100.80"


def _throttle():
    clock = FrozenClock()
    return LoginThrottle(clock=clock), clock


def test_an_attempt_counts_from_the_moment_it_is_let_through():
    throttle, clock = _throttle()
    for _ in range(FREE_FAILURES):
        wait, attempt = throttle.begin(CLIENT, "owner")
        assert wait is None and attempt is not None
        throttle.failed(attempt)
    clock.advance(2)

    wait, first = throttle.begin(CLIENT, "owner")
    assert wait is None and first is not None
    # Still being checked: the next request already waits.
    wait, second = throttle.begin(CLIENT, "owner")
    assert second is None and wait is not None and wait >= 1


def test_a_success_clears_the_pair_but_not_the_ceiling():
    throttle, clock = _throttle()
    for n in range(CLIENT_CEILING - 1):
        _, attempt = throttle.begin(CLIENT, f"user{n}")
        throttle.failed(attempt)

    _, attempt = throttle.begin(CLIENT, "owner")
    throttle.succeeded(attempt)

    # The owner's own reservation is gone; the 19 failures stay.
    assert throttle.sizes() == (CLIENT_CEILING - 1, 1)
    _, last = throttle.begin(CLIENT, "user-last")
    throttle.failed(last)
    assert throttle.retry_after(CLIENT, "someone-else") is not None


def test_a_cancelled_attempt_leaves_nothing_behind():
    throttle, _ = _throttle()

    _, attempt = throttle.begin(CLIENT, "owner")
    throttle.cancel(attempt)

    assert throttle.sizes() == (0, 0)
    assert throttle.retry_after(CLIENT, "owner") is None


def test_settling_twice_or_late_is_harmless():
    throttle, _ = _throttle()
    _, attempt = throttle.begin(CLIENT, "owner")
    throttle.cancel(attempt)

    throttle.cancel(attempt)
    throttle.succeeded(attempt)

    assert throttle.sizes() == (0, 0)


def test_begin_and_retry_after_agree_on_the_wait():
    throttle, clock = _throttle()
    for _ in range(FREE_FAILURES + 2):
        _, attempt = throttle.begin(CLIENT, "owner")
        if attempt is None:
            clock.advance(31)
            _, attempt = throttle.begin(CLIENT, "owner")
        throttle.failed(attempt)

    expected = throttle.retry_after(CLIENT, "owner")
    wait, attempt = throttle.begin(CLIENT, "owner")

    assert attempt is None and wait == expected
