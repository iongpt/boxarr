"""The admin metadata repair picks each entry's film by its Mojo IMDb id (#141).

The repair used to group entries by title and give every entry of a title the
same lookup result (poster first, then the newest), so a week charting the
1978 "Superman" got the 2025 film's TMDB id, poster and year. Entries that
store Box Office Mojo's IMDb id are now repaired with the film carrying it;
entries without one (weeks stored before v2.2.0) keep the old heuristic.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.api.routes import admin as admin_route

SUPERMAN_1978 = {
    "tmdbId": 1924,
    "title": "Superman",
    "year": 1978,
    "imdbId": "tt0078346",
    "remotePoster": "https://img/1924.jpg",
}
SUPERMAN_2025 = {
    "tmdbId": 1061474,
    "title": "Superman",
    "year": 2025,
    "imdbId": "tt5950044",
    "remotePoster": "https://img/1061474.jpg",
}


class _FakeRadarr:
    lookups: dict = {}
    terms: list = []

    def __init__(self, *_, **__):
        pass

    def search_movie(self, term):
        _FakeRadarr.terms.append(term)
        return list(_FakeRadarr.lookups.get(term, []))


def _entry(title, bom_imdb_id):
    return {
        "rank": 1,
        "title": title,
        "radarr_id": None,
        "poster": None,
        "tmdb_id": None,
        "year": None,
        "bom_imdb_id": bom_imdb_id,
    }


@pytest.fixture
def weeks(tmp_path, monkeypatch):
    monkeypatch.setattr(
        admin_route,
        "settings",
        SimpleNamespace(radarr_api_key="test-key", boxarr_data_directory=tmp_path),
    )
    monkeypatch.setattr(admin_route, "RadarrService", _FakeRadarr)
    _FakeRadarr.lookups = {}
    _FakeRadarr.terms = []
    directory = tmp_path / "weekly_pages"
    directory.mkdir()

    def _write(name, *movies):
        path = directory / f"{name}.json"
        path.write_text(json.dumps({"movies": list(movies)}))
        return path

    return _write


def _repair(dry_run=False):
    request = admin_route.RepairRequest(dry_run=dry_run, rate_limit_delay=0)

    async def _drain():
        response = await admin_route.repair_missing_metadata(request)
        return "".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(_drain())


def _movie(path):
    return json.loads(path.read_text())["movies"][0]


def test_same_title_entries_get_the_film_with_their_mojo_imdb_id(weeks):
    _FakeRadarr.lookups = {"Superman": [SUPERMAN_1978, SUPERMAN_2025]}
    rerelease = weeks("2024W10", _entry("Superman", "tt0078346"))
    new = weeks("2025W28", _entry("Superman", "tt5950044"))

    stream = _repair()

    assert '"stage": "complete"' in stream
    assert (_movie(rerelease)["tmdb_id"], _movie(rerelease)["year"]) == (1924, 1978)
    assert (_movie(new)["tmdb_id"], _movie(new)["year"]) == (1061474, 2025)


def test_film_missing_from_the_title_search_is_found_by_its_imdb_id(weeks):
    _FakeRadarr.lookups = {
        "Superman": [SUPERMAN_1978],
        "imdb:tt5950044": [SUPERMAN_2025],
    }
    new = weeks("2025W28", _entry("Superman", "tt5950044"))

    _repair()

    assert _movie(new)["tmdb_id"] == 1061474
    assert _FakeRadarr.terms == ["Superman", "imdb:tt5950044"]


def test_lookup_finding_only_another_film_repairs_nothing(weeks):
    """TMDB lists only the 1978 film: the 2025 entry does not get its TMDB
    id, poster or year (the status refresh would only clear them again)."""
    _FakeRadarr.lookups = {"Superman": [SUPERMAN_1978]}
    new = weeks("2025W28", _entry("Superman", "tt5950044"))
    before = new.read_text()

    stream = _repair()

    assert '"stage": "complete"' in stream
    assert new.read_text() == before
    assert _FakeRadarr.terms == ["Superman", "imdb:tt5950044"]


def test_entry_without_a_mojo_imdb_id_keeps_the_poster_then_newest_pick(weeks):
    no_poster = dict(SUPERMAN_2025, tmdbId=99, year=2030, remotePoster=None)
    _FakeRadarr.lookups = {"Superman": [SUPERMAN_1978, no_poster, SUPERMAN_2025]}
    legacy = weeks("2025W28", _entry("Superman", None))

    _repair()

    assert _movie(legacy)["tmdb_id"] == 1061474


def test_dry_run_lists_titles(weeks):
    _FakeRadarr.lookups = {"Superman": [SUPERMAN_1978, SUPERMAN_2025]}
    weeks("2024W10", _entry("Superman", "tt0078346"))
    weeks("2025W28", _entry("Superman", "tt5950044"))

    stream = _repair(dry_run=True)

    complete = json.loads(stream.strip().split("data: ")[-1])
    assert complete["would_fix_movies"] == 2
    assert complete["movies_found"] == ["Superman", "Superman"]
