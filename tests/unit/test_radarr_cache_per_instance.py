"""Radarr's library and quality profiles are cached per Radarr (#137).

The module-level caches used to be keyless: after the Radarr URL or API key
changed in /setup, a fresh RadarrService for the new Radarr was served the
previous one's library or quality profiles - with no call to the new one -
for as long as the cache was fresh. A film only in the old library showed as
in the new one, and films were added with the old Radarr's profile id.

Radarr is faked at the HTTP transport, under the real RadarrService.
"""

from typing import Any, Dict, List

import httpx
import pytest

import src.core.radarr as core_radarr
from src.core.radarr import RadarrService

URL_A, KEY_A = "http://radarr-a:7878", "key-A"
URL_B, KEY_B = "http://radarr-b:7878", "key-B"


class FakeRadarr:
    def __init__(self, title: str, profile_id: int):
        self.asks: List[str] = []
        self.answers: Dict[str, Any] = {
            "/api/v3/movie": [{"id": profile_id, "title": title, "tmdbId": 1}],
            "/api/v3/qualityProfile": [{"id": profile_id, "name": "HD-1080p"}],
        }

    def service(self, url: str, key: str) -> RadarrService:
        def answer(request: httpx.Request) -> httpx.Response:
            self.asks.append(request.url.path)
            return httpx.Response(200, json=self.answers[request.url.path])

        client = httpx.Client(base_url=url, transport=httpx.MockTransport(answer))
        return RadarrService(url=url, api_key=key, http_client=client)


@pytest.fixture(autouse=True)
def empty_caches(monkeypatch):
    monkeypatch.setattr(core_radarr, "_movies_cache", {"ts": 0.0, "data": []})
    monkeypatch.setattr(core_radarr, "_profiles_cache", {"ts": 0.0, "data": []})


@pytest.fixture
def radarr_a() -> FakeRadarr:
    return FakeRadarr("Only In A", 1)


@pytest.fixture
def radarr_b() -> FakeRadarr:
    return FakeRadarr("Only In B", 7)


def _titles(movies) -> List[str]:
    return [movie.title for movie in movies]


def test_a_new_radarr_is_not_served_the_previous_one_library(radarr_a, radarr_b):
    assert _titles(radarr_a.service(URL_A, KEY_A).get_all_movies()) == ["Only In A"]

    assert _titles(radarr_b.service(URL_B, KEY_B).get_all_movies()) == ["Only In B"]
    assert radarr_b.asks == ["/api/v3/movie"]


def test_a_new_api_key_is_not_served_the_previous_key_library(radarr_a, radarr_b):
    radarr_a.service(URL_A, KEY_A).get_all_movies()

    assert _titles(radarr_b.service(URL_A, "regenerated-key").get_all_movies()) == [
        "Only In B"
    ]


def test_a_new_radarr_is_not_served_the_previous_one_profiles(radarr_a, radarr_b):
    assert [p.id for p in radarr_a.service(URL_A, KEY_A).get_quality_profiles()] == [1]

    assert [p.id for p in radarr_b.service(URL_B, KEY_B).get_quality_profiles()] == [7]
    assert radarr_b.asks == ["/api/v3/qualityProfile"]


def test_the_same_radarr_is_still_served_from_the_cache(radarr_a):
    radarr_a.service(URL_A, KEY_A).get_all_movies()
    radarr_a.service(URL_A, KEY_A).get_quality_profiles()

    assert _titles(radarr_a.service(URL_A, KEY_A).get_all_movies()) == ["Only In A"]
    assert [p.id for p in radarr_a.service(URL_A, KEY_A).get_quality_profiles()] == [1]
    assert radarr_a.asks == ["/api/v3/movie", "/api/v3/qualityProfile"]
