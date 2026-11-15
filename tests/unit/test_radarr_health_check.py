"""How /api/health learns whether Radarr answers (#138).

The check runs off the event loop, one at a time, and its answer is reused for
a while, so the healthcheck never waits on Radarr for long and never queues a
call per probe. These tests drive RadarrHealthCheck with a fake probe; the
hung-socket end-to-end tests are in tests/integration/test_hung_radarr.py.
"""

import asyncio
import threading
import time
from typing import List, Tuple

import pytest

import src.api.health as health
from src.api.health import RadarrHealthCheck

URL = "http://radarr.test:7878"
KEY = "health-test-key"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(health.settings, "radarr_url", URL)
    monkeypatch.setattr(health.settings, "radarr_api_key", KEY)


class FakeProbe:
    """Stands in for asking Radarr; records each ask."""

    def __init__(self, answer: bool = True, hang: bool = False) -> None:
        self.answer = answer
        self.asks: List[Tuple[Tuple[str, str], float]] = []
        self.hang_up = threading.Event()
        if not hang:
            self.hang_up.set()

    def __call__(self, credentials, timeout) -> bool:
        self.asks.append((credentials, timeout))
        self.hang_up.wait(10)
        return self.answer


def _probe(check: RadarrHealthCheck, times: int = 1) -> List[bool]:
    async def probes():
        return [await check.connected() for _ in range(times)]

    return asyncio.run(probes())


def test_a_reachable_radarr_is_reported_connected_and_reused(configured):
    probe = FakeProbe(answer=True)
    check = RadarrHealthCheck(probe=probe, wait=2.0)

    # The first probe waits for the check, so it reports the real state - as
    # soon as the check has it, not after the whole wait.
    started = time.monotonic()
    assert _probe(check) == [True]
    assert time.monotonic() - started < 1.0
    assert _probe(check, times=2) == [True, True]
    assert probe.asks == [((URL, KEY), 5.0)]


def test_an_unreachable_radarr_is_reported_not_connected(configured):
    probe = FakeProbe(answer=False)

    assert _probe(RadarrHealthCheck(probe=probe)) == [False]


def test_a_failing_probe_is_not_connected(configured):
    def probe(credentials, timeout):
        raise RuntimeError("boom")

    check = RadarrHealthCheck(probe=probe)

    assert _probe(check) == [False]


def test_the_answer_expires(configured):
    probe = FakeProbe()
    check = RadarrHealthCheck(probe=probe, ttl=0)

    assert _probe(check, times=2) == [True, True]
    assert len(probe.asks) == 2


def test_new_credentials_are_checked_again(configured, monkeypatch):
    probe = FakeProbe()
    check = RadarrHealthCheck(probe=probe)
    _probe(check)

    monkeypatch.setattr(health.settings, "radarr_url", "http://new-radarr:7878")
    assert _probe(check) == [True]

    assert [credentials for credentials, _ in probe.asks] == [
        (URL, KEY),
        ("http://new-radarr:7878", KEY),
    ]


def test_a_hung_check_is_waited_for_briefly_and_only_once(configured):
    probe = FakeProbe(answer=True, hang=True)
    check = RadarrHealthCheck(probe=probe, wait=0.3)
    try:
        started = time.monotonic()
        first = _probe(check)
        first_took = time.monotonic() - started
        started = time.monotonic()
        later = _probe(check, times=5)
        later_took = time.monotonic() - started
    finally:
        probe.hang_up.set()
        check.join(5)

    # Nothing is known yet, so the probes say "not connected" - quickly.
    assert first == [False] and later == [False] * 5
    assert 0.25 < first_took < 1.0
    assert later_took < 0.25
    assert len(probe.asks) == 1
    # Once the check finishes, its answer is what the next probe reports.
    assert _probe(check) == [True]
    assert len(probe.asks) == 1


def test_a_hung_check_keeps_the_last_known_answer(configured):
    probe = FakeProbe(answer=True)
    check = RadarrHealthCheck(probe=probe, ttl=0, wait=0.1)
    assert _probe(check) == [True]

    probe.hang_up.clear()  # Radarr hangs from now on
    try:
        assert _probe(check, times=3) == [True, True, True]
    finally:
        probe.hang_up.set()
        check.join(5)
    assert len(probe.asks) == 2


def test_an_unconfigured_radarr_is_never_asked(monkeypatch):
    monkeypatch.setattr(health.settings, "radarr_api_key", "")
    probe = FakeProbe()

    assert _probe(RadarrHealthCheck(probe=probe)) == [False]
    assert probe.asks == []


def test_the_default_check_asks_radarr_with_the_short_timeout(configured, monkeypatch):
    built = []

    class _FakeRadarrService:
        def __init__(self, **kwargs):
            built.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def test_connection(self):
            return True

    monkeypatch.setattr(health, "RadarrService", _FakeRadarrService)
    check = RadarrHealthCheck()

    assert _probe(check) == [True]
    check.join(5)
    assert built == [{"url": URL, "api_key": KEY, "timeout": 5.0}]
