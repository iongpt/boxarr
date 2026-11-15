"""Auto-add honours ``radarr.search_for_movie`` (issue #146).

The manual "Add to Radarr" button always passed the setting through, but the
auto-add loop hard-coded ``True``, so ``search_for_movie: false`` in YAML (or
``RADARR_SEARCH_FOR_MOVIE=false``) still made Radarr search the indexers for
every movie Boxarr added on its own.
"""

import httpx
import pytest

from src.core import auto_add
from src.core.auto_add import auto_add_missing_movies
from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MatchResult
from src.core.radarr import RadarrService
from src.utils.config import Settings


class _FakeQualityProfile:
    id = 1
    name = "HD-1080p"


class _FakeAddedMovie:
    def __init__(self, title):
        self.title = title


class _FakeIgnoreList:
    def get_ignored_tmdb_ids(self):
        return set()


class _FakeRootFolderManager:
    def __init__(self, *_, **__):
        pass

    def determine_root_folder(self, genres=None, movie_title=None):
        return "/movies"


class _RecordingRadarrService:
    """Radarr double whose add_movie takes the flags positionally.

    The other auto-add test doubles share this signature, which is why the
    loop passes the setting positionally rather than by keyword.
    """

    def __init__(self):
        self.add_calls = []

    def get_quality_profiles(self):
        return [_FakeQualityProfile()]

    def search_movie(self, title):
        return [
            {
                "tmdbId": 101,
                "title": title,
                "year": 2025,
                "genres": ["Action"],
                "originalLanguage": {"id": 1, "name": "English"},
            }
        ]

    def add_movie(self, tmdb_id, profile_id, root_folder, monitored, search):
        self.add_calls.append({"tmdb_id": tmdb_id, "search": search})
        return _FakeAddedMovie(f"movie-{tmdb_id}")


def _chart():
    return [
        MatchResult(
            box_office_movie=BoxOfficeMovie(
                rank=1, title="New Release", weekend_gross=5_000_000.0
            )
        )
    ]


@pytest.fixture
def run_auto_add(monkeypatch):
    """Run the real auto-add loop with the given settings and Radarr double."""
    monkeypatch.setattr(auto_add, "IgnoreList", _FakeIgnoreList)
    monkeypatch.setattr(auto_add, "RootFolderManager", _FakeRootFolderManager)

    def _run(config, service):
        monkeypatch.setattr(auto_add, "settings", config)
        return auto_add_missing_movies(_chart(), service, 2025)

    return _run


class TestAutoAddSearchSetting:
    def test_search_disabled_is_passed_to_radarr(self, run_auto_add):
        service = _RecordingRadarrService()

        added = run_auto_add(
            Settings(radarr_api_key="test", radarr_search_for_movie=False), service
        )

        assert added == ["movie-101"]
        assert service.add_calls == [{"tmdb_id": 101, "search": False}]

    def test_search_stays_on_by_default(self, run_auto_add):
        service = _RecordingRadarrService()

        run_auto_add(Settings(radarr_api_key="test"), service)

        assert service.add_calls == [{"tmdb_id": 101, "search": True}]

    def test_environment_opt_out_reaches_auto_add(self, run_auto_add, monkeypatch):
        monkeypatch.setenv("RADARR_SEARCH_FOR_MOVIE", "false")
        service = _RecordingRadarrService()

        run_auto_add(Settings(radarr_api_key="test"), service)

        assert service.add_calls == [{"tmdb_id": 101, "search": False}]


class _FakeRadarrHttp:
    """Just enough of Radarr's HTTP API for a lookup, a tag and an add."""

    BASE_URL = "http://radarr.local:7878"

    def __init__(self):
        self.posted_movies = []

    def request(self, method, endpoint, **kwargs):
        request = httpx.Request(method, f"{self.BASE_URL}{endpoint}")
        if method == "GET" and endpoint == "/api/v3/movie/lookup":
            return httpx.Response(
                200,
                request=request,
                json=[
                    {
                        "tmdbId": 101,
                        "title": "New Release",
                        "titleSlug": "new-release-101",
                        "year": 2025,
                        "genres": ["Action"],
                        "originalLanguage": {"id": 1, "name": "English"},
                    }
                ],
            )
        if endpoint == "/api/v3/tag":
            if method == "GET":
                return httpx.Response(200, request=request, json=[])
            return httpx.Response(
                200, request=request, json={"id": 3, "label": "boxarr"}
            )
        if method == "POST" and endpoint == "/api/v3/movie":
            body = kwargs.get("json", {})
            self.posted_movies.append(body)
            return httpx.Response(200, request=request, json={"id": 7, **body})
        return httpx.Response(404, request=request, json={})

    def close(self):
        pass


def test_auto_added_movie_is_posted_without_a_search(run_auto_add, monkeypatch):
    """End to end through the real RadarrService: the payload says no search."""
    http = _FakeRadarrHttp()
    service = RadarrService(url=http.BASE_URL, api_key="test", http_client=http)
    # Bypass the module-level profile cache other tests may have filled.
    monkeypatch.setattr(
        service, "get_quality_profiles", lambda *_, **__: [_FakeQualityProfile()]
    )

    added = run_auto_add(
        Settings(radarr_api_key="test", radarr_search_for_movie=False), service
    )

    assert added == ["New Release"]
    assert [body["addOptions"]["searchForMovie"] for body in http.posted_movies] == [
        False
    ]
