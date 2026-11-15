"""The Radarr library is downloaded once however many callers ask (#138).

The routes that ask Radarr now run in the threadpool instead of on the event
loop. That loop used to serialise them by accident; without a lock, cache
misses arriving together would each download the whole /api/v3/movie list (a
big library takes seconds), or each wait out a hung Radarr in turn.

Each Radarr's library is downloaded on its own, so a hung Radarr never holds
up a request to the one the user just switched to, and a status poll can give
up on a download another request is waiting for.

Radarr is faked at the HTTP transport, under the real RadarrService.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List

import httpx
import pytest

import src.core.radarr as core_radarr
from src.core.exceptions import RadarrConnectionError
from src.core.radarr import LibraryDownloadPending, RadarrService

URL = "http://radarr.test:7878"
KEY = "library-test-key"
CALLERS = 8


def _film(tmdb_id: int) -> Dict[str, Any]:
    return {"id": tmdb_id, "title": f"Film {tmdb_id}", "tmdbId": tmdb_id}


class FakeRadarr:
    """Answers /api/v3/movie only once the test lets it, counting each ask."""

    def __init__(self) -> None:
        self.asks: List[str] = []
        self.libraries: List[List[Dict[str, Any]]] = [[_film(1)]]
        self.fail = False
        self._lock = threading.Lock()
        self._answer = threading.Semaphore(0)

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            number = len(self.asks)
            self.asks.append(request.url.path)
        assert self._answer.acquire(timeout=10), "the test never let Radarr answer"
        if self.fail:
            raise httpx.ConnectError("Radarr is down", request=request)
        library = self.libraries[min(number, len(self.libraries) - 1)]
        return httpx.Response(200, json=library)

    def answer(self, times: int = 1) -> None:
        for _ in range(times):
            self._answer.release()

    def service(self, url: str = URL, key: str = KEY) -> RadarrService:
        client = httpx.Client(base_url=url, transport=httpx.MockTransport(self.handler))
        return RadarrService(url=url, api_key=key, http_client=client)

    def wait_for_asks(self, count: int) -> None:
        deadline = time.monotonic() + 5
        while len(self.asks) < count:
            assert time.monotonic() < deadline, f"Radarr was asked {self.asks}"
            time.sleep(0.01)


@pytest.fixture
def radarr(monkeypatch) -> FakeRadarr:
    monkeypatch.setattr(core_radarr, "_movies_cache", {"ts": 0.0, "data": []})
    return FakeRadarr()


def _tmdb_ids(movies) -> List[int]:
    return [m.tmdbId for m in movies]


def _in_threads(calls: List[Callable[[], Any]]):
    pool = ThreadPoolExecutor(len(calls))
    return pool, [pool.submit(call) for call in calls]


def _settle() -> None:
    """Give callers that were started time to reach Radarr or the lock."""
    time.sleep(0.2)


def test_concurrent_cache_misses_download_the_library_once(radarr):
    pool, futures = _in_threads([radarr.service().get_all_movies] * CALLERS)
    with pool:
        radarr.wait_for_asks(1)
        _settle()  # the other callers are now waiting, not downloading
        radarr.answer(CALLERS)  # enough for every caller, should each ask
        results = [f.result(timeout=10) for f in futures]

    assert radarr.asks == ["/api/v3/movie"]
    assert all(_tmdb_ids(movies) == [1] for movies in results)
    # The download is cached for the callers after them too.
    assert _tmdb_ids(radarr.service().get_all_movies()) == [1]
    assert len(radarr.asks) == 1


def test_a_failed_download_fails_every_waiting_caller_once(radarr):
    radarr.fail = True
    pool, futures = _in_threads([radarr.service().get_all_movies] * CALLERS)
    with pool:
        radarr.wait_for_asks(1)
        _settle()
        radarr.answer(CALLERS)
        errors = []
        for future in futures:
            with pytest.raises(RadarrConnectionError):
                future.result(timeout=10)
            errors.append(future.exception())

    # A hung or failing Radarr is waited out once, not once per caller in turn.
    assert radarr.asks == ["/api/v3/movie"]
    assert len(errors) == CALLERS
    # Failures are not cached: the next caller asks again.
    radarr.fail = False
    radarr.answer()
    assert _tmdb_ids(radarr.service().get_all_movies()) == [1]
    assert len(radarr.asks) == 2


def test_a_cache_bypass_waits_for_a_download_that_began_after_it_asked(radarr):
    # Before an add, Radarr lists film 1; afterwards it also lists film 2.
    radarr.libraries = [[_film(1)], [_film(1), _film(2)]]
    pool = ThreadPoolExecutor(3)
    with pool:
        earlier = pool.submit(radarr.service().get_all_movies)
        radarr.wait_for_asks(1)
        fresh = pool.submit(radarr.service().get_all_movies, ignore_cache=True)
        cached = pool.submit(radarr.service().get_all_movies)
        _settle()
        assert radarr.asks == ["/api/v3/movie"]  # the bypass waits its turn
        radarr.answer()
        radarr.wait_for_asks(2)
        radarr.answer()

        assert _tmdb_ids(earlier.result(timeout=10)) == [1]
        # A caller that may use the cache shares the download under way...
        assert _tmdb_ids(cached.result(timeout=10)) == [1]
        # ...one that bypasses it gets a library fetched after it asked.
        assert _tmdb_ids(fresh.result(timeout=10)) == [1, 2]

    assert len(radarr.asks) == 2
    assert _tmdb_ids(core_radarr._movies_cache["data"]) == [1, 2]


@pytest.mark.parametrize("bypass_fails", [False, True], ids=["answers", "fails"])
def test_a_fresh_cache_is_served_while_a_bypass_is_stuck(radarr, bypass_fails):
    radarr.answer()
    assert _tmdb_ids(radarr.service().get_all_movies()) == [1]  # warm cache

    radarr.libraries = [[_film(1)], [_film(1), _film(2)]]
    pool = ThreadPoolExecutor(1)
    with pool:
        bypass = pool.submit(radarr.service().get_all_movies, ignore_cache=True)
        radarr.wait_for_asks(2)  # stuck at Radarr
        # The status polls keep getting the cached library at once.
        started = time.monotonic()
        assert _tmdb_ids(radarr.service().get_all_movies()) == [1]
        assert time.monotonic() - started < 0.5
        radarr.fail = bypass_fails
        radarr.answer()
        if bypass_fails:
            with pytest.raises(RadarrConnectionError):
                bypass.result(timeout=10)
        else:
            assert _tmdb_ids(bypass.result(timeout=10)) == [1, 2]

    # A failed bypass leaves the cache as it was; a good one refreshes it.
    expected = [1] if bypass_fails else [1, 2]
    assert _tmdb_ids(radarr.service().get_all_movies()) == expected
    assert len(radarr.asks) == 2


def test_a_download_that_raced_an_invalidation_is_not_cached(radarr):
    radarr.libraries = [[_film(1)], [_film(1), _film(2)]]
    pool = ThreadPoolExecutor(2)
    with pool:
        before_add = pool.submit(radarr.service().get_all_movies)
        radarr.wait_for_asks(1)
        # A movie is added (add_movie invalidates) while that download runs.
        core_radarr._invalidate_library_cache()
        after_add = pool.submit(radarr.service().get_all_movies)
        _settle()
        radarr.answer(2)
        assert _tmdb_ids(before_add.result(timeout=10)) == [1]
        # A caller that asked after the add does not get the pre-add library.
        assert _tmdb_ids(after_add.result(timeout=10)) == [1, 2]

    # The pre-add library was not stored, so the added film shows at once.
    assert _tmdb_ids(radarr.service().get_all_movies()) == [1, 2]
    assert len(radarr.asks) == 2


def test_a_cache_bypass_takes_the_failure_of_a_download_under_way(radarr):
    radarr.fail = True
    pool = ThreadPoolExecutor(2)
    with pool:
        earlier = pool.submit(radarr.service().get_all_movies)
        radarr.wait_for_asks(1)
        fresh = pool.submit(radarr.service().get_all_movies, ignore_cache=True)
        _settle()
        radarr.answer(2)  # enough for the bypass, should it ask again
        for caller in (earlier, fresh):
            with pytest.raises(RadarrConnectionError):
                caller.result(timeout=10)

    # Radarr failed after the bypass asked: that is its answer, so a hung
    # Radarr is waited out once, not twice in a row.
    assert radarr.asks == ["/api/v3/movie"]


@pytest.mark.parametrize("ignore_cache", [False, True], ids=["cached", "bypass"])
def test_a_stuck_download_does_not_hold_up_another_radarr(radarr, ignore_cache):
    # The configured Radarr hangs, and the user saves another one in /setup.
    other = FakeRadarr()
    other.libraries = [[_film(7)]]
    other.answer()
    pool = ThreadPoolExecutor(1)
    with pool:
        stuck = pool.submit(radarr.service().get_all_movies)
        radarr.wait_for_asks(1)
        started = time.monotonic()
        movies = other.service("http://other.test", "other-key").get_all_movies(
            ignore_cache=ignore_cache
        )
        took = time.monotonic() - started
        # The old Radarr answers after all, late.
        radarr.answer()
        assert _tmdb_ids(stuck.result(timeout=10)) == [1]

    assert _tmdb_ids(movies) == [7]
    assert took < 1.0
    assert len(radarr.asks) == 1 and len(other.asks) == 1
    # The old Radarr's late library was not stored over the newer download.
    assert _tmdb_ids(core_radarr._movies_cache["data"]) == [7]


def test_a_caller_with_a_wait_gives_up_on_a_stuck_download(radarr):
    pool = ThreadPoolExecutor(2)
    with pool:
        stuck = pool.submit(radarr.service().get_all_movies)
        radarr.wait_for_asks(1)
        # A download that finishes within the wait is shared as usual.
        patient = pool.submit(radarr.service().get_all_movies, wait=5.0)
        _settle()
        radarr.answer()
        assert _tmdb_ids(stuck.result(timeout=10)) == [1]
        assert _tmdb_ids(patient.result(timeout=10)) == [1]

        core_radarr._invalidate_library_cache()
        stuck = pool.submit(radarr.service().get_all_movies)
        radarr.wait_for_asks(2)
        started = time.monotonic()
        with pytest.raises(LibraryDownloadPending):
            radarr.service().get_all_movies(wait=0.5)
        first_took = time.monotonic() - started
        # The wait counts from the download's start, so later polls give up
        # at once instead of each waiting again.
        started = time.monotonic()
        with pytest.raises(LibraryDownloadPending):
            radarr.service().get_all_movies(wait=0.5)
        later_took = time.monotonic() - started
        radarr.answer()
        assert _tmdb_ids(stuck.result(timeout=10)) == [1]

    assert 0.2 < first_took < 1.0
    assert later_took < 0.1
    # Giving up never started a download of its own.
    assert len(radarr.asks) == 2
    assert _tmdb_ids(radarr.service().get_all_movies(wait=0.5)) == [1]
    assert len(radarr.asks) == 2


# --------------------------------------------------------------------------
# Timeouts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "radarr_timeout, connect", [(120.0, 10.0), (300.0, 10.0), (5.0, 5.0)]
)
def test_connecting_gives_up_sooner_than_reading(monkeypatch, radarr_timeout, connect):
    monkeypatch.setattr(core_radarr.settings, "radarr_timeout", radarr_timeout)

    with RadarrService(url=URL, api_key=KEY) as service:
        timeout = service.client.timeout

    # A big library still gets the whole read timeout (#62); an unreachable
    # host fails within the connect timeout (#138).
    assert timeout.read == radarr_timeout
    assert timeout.write == radarr_timeout
    assert timeout.pool == radarr_timeout
    assert timeout.connect == connect


def test_an_explicit_timeout_overrides_the_setting(monkeypatch):
    monkeypatch.setattr(core_radarr.settings, "radarr_timeout", 120.0)

    with RadarrService(url=URL, api_key=KEY, timeout=3.0) as service:
        assert service.client.timeout == httpx.Timeout(3.0)
