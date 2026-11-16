"""Login throttling (#151, design §3.8.3, amendment A3).

Keyed on (client, normalised username) with a looser per-client ceiling;
only password failures count; no hard or global lockout.
"""

import logging

import pytest

from src.api.auth.throttle import (
    CLIENT_CEILING,
    FREE_FAILURES,
    MAX_DELAY_SECONDS,
    WINDOW_SECONDS,
    CredentialKind,
    LoginThrottle,
    backoff,
    client_key,
)
from src.api.client_address import parse_trusted_proxies


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def throttle(clock):
    return LoginThrottle(clock=clock)


def _fail(throttle, client, username, times=1, kind=CredentialKind.PASSWORD):
    for _ in range(times):
        throttle.record_failure(client, username, kind)


def test_the_backoff_schedule():
    assert [backoff(n, 5) for n in range(12)] == [0, 0, 0, 0, 0, 1, 2, 4, 8, 16, 30, 30]
    assert backoff(100, 5) == MAX_DELAY_SECONDS == 30


def test_five_failures_are_free(throttle):
    _fail(throttle, "203.0.113.9", "admin", FREE_FAILURES - 1)
    assert throttle.retry_after("203.0.113.9", "admin") is None

    _fail(throttle, "203.0.113.9", "admin")
    assert throttle.retry_after("203.0.113.9", "admin") == 1


def test_the_wait_counts_from_the_last_failure(throttle, clock):
    _fail(throttle, "203.0.113.9", "admin", FREE_FAILURES + 3)  # 8 s
    clock.advance(5)

    assert throttle.retry_after("203.0.113.9", "admin") == 3
    clock.advance(3)
    assert throttle.retry_after("203.0.113.9", "admin") is None


def test_the_wait_is_capped_at_30_seconds(throttle):
    _fail(throttle, "203.0.113.9", "admin", 40)

    assert throttle.retry_after("203.0.113.9", "admin") == 30


def test_failures_expire_after_15_minutes(throttle, clock):
    _fail(throttle, "203.0.113.9", "admin", 10)
    clock.advance(WINDOW_SECONDS)

    assert throttle.retry_after("203.0.113.9", "admin") is None
    assert throttle.sizes() == (0, 0)  # both entries were dropped on read


def test_an_attacker_without_the_username_cannot_slow_the_owner(throttle):
    _fail(throttle, "203.0.113.9", "admin", 15)  # the attacker guesses "admin"

    assert throttle.retry_after("198.51.100.7", "owner") is None
    assert throttle.retry_after("198.51.100.7", "admin") is None  # other client
    assert throttle.retry_after("203.0.113.9", "owner") is None  # under ceiling


def test_usernames_are_normalised_for_the_key(throttle):
    _fail(throttle, "203.0.113.9", "Admin", 3)
    _fail(throttle, "203.0.113.9", " ADMIN", 3)

    assert throttle.retry_after("203.0.113.9", "admin") == 2


def test_the_per_client_ceiling_bounds_username_spraying(throttle):
    for n in range(CLIENT_CEILING - 1):
        _fail(throttle, "203.0.113.9", f"user{n}")
    assert throttle.retry_after("203.0.113.9", "someone-new") is None

    _fail(throttle, "203.0.113.9", "user-last")

    assert throttle.retry_after("203.0.113.9", "someone-new") == 1
    assert throttle.retry_after("198.51.100.7", "someone-new") is None


@pytest.mark.parametrize("kind", [CredentialKind.API_KEY, CredentialKind.WIDGET_TOKEN])
def test_api_key_and_widget_token_failures_never_count(throttle, kind):
    _fail(throttle, "203.0.113.9", "admin", 100, kind=kind)
    _fail(throttle, "203.0.113.9", None, 100, kind=kind)

    assert throttle.retry_after("203.0.113.9", "admin") is None
    assert throttle.retry_after("203.0.113.9", "") is None
    assert throttle.sizes() == (0, 0)


def test_a_success_clears_the_pair_but_not_the_ceiling(throttle):
    for n in range(CLIENT_CEILING - 3):
        _fail(throttle, "203.0.113.9", f"user{n}")
    _fail(throttle, "203.0.113.9", "admin", 3)
    throttle.record_success("203.0.113.9", "admin")

    assert throttle.retry_after("203.0.113.9", "admin") == 1  # ceiling holds


def test_a_success_lets_the_owner_straight_back_in(throttle):
    _fail(throttle, "203.0.113.9", "admin", 7)
    throttle.record_success("203.0.113.9", "ADMIN")

    assert throttle.retry_after("203.0.113.9", "admin") is None


@pytest.mark.parametrize(
    "client, key",
    [
        ("203.0.113.9", "203.0.113.9"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::ffff", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("testclient", "testclient"),
        (None, "-"),
        ("", "-"),
    ],
)
def test_client_keys(client, key):
    assert client_key(client) == key


def test_ipv6_clients_in_one_64_share_a_budget(throttle):
    _fail(throttle, "2001:db8:1:2::1", "admin", 3)
    _fail(throttle, "2001:db8:1:2::2", "admin", 3)

    assert throttle.retry_after("2001:db8:1:2::3", "admin") == 2
    assert throttle.retry_after("2001:db8:1:3::1", "admin") is None


def test_the_tables_are_bounded(clock):
    throttle = LoginThrottle(clock=clock, max_entries=100)
    for n in range(300):
        _fail(throttle, f"10.0.{n // 250}.{n % 250}", "admin")

    assert throttle.sizes() == (100, 100)


def test_bad_keys_are_logged_once_per_client_every_10_minutes(throttle, clock):
    assert throttle.should_log_bad_key("203.0.113.9") is True
    assert throttle.should_log_bad_key("203.0.113.9") is False
    assert throttle.should_log_bad_key("198.51.100.7") is True
    clock.advance(600)
    assert throttle.should_log_bad_key("203.0.113.9") is True


def test_a_shared_private_address_gets_the_proxy_hint_once(clock, caplog):
    throttle = LoginThrottle(clock=clock)

    with caplog.at_level(logging.WARNING, logger="src.api.auth.throttle"):
        for n in range(40):
            throttle.record_failure("172.18.0.2", f"user{n % 3}")

    hints = [
        r for r in caplog.records if "BOXARR_AUTH_TRUSTED_PROXIES" in r.getMessage()
    ]
    assert len(hints) == 1 and "172.18.0.2" in hints[0].getMessage()


def test_no_hint_for_a_trusted_or_public_address(clock, caplog):
    trusted, _ = parse_trusted_proxies("172.18.0.2")
    with caplog.at_level(logging.WARNING, logger="src.api.auth.throttle"):
        known = LoginThrottle(clock=clock, trusted=lambda: trusted)
        public = LoginThrottle(clock=clock)
        for _ in range(40):
            known.record_failure("172.18.0.2", "admin")
            # Python counts the documentation ranges as private: use a
            # routable address.
            public.record_failure("1.1.1.1", "admin")

    assert "BOXARR_AUTH_TRUSTED_PROXIES" not in caplog.text


def test_there_is_no_global_lockout(throttle):
    for n in range(2000):
        _fail(throttle, f"10.{n // 250}.{n % 250}.1", "admin")

    assert throttle.retry_after("198.51.100.7", "admin") is None
