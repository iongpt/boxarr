"""Auto-add over a list merged from several box office regions.

One pass over every region's unmatched films in chart order (#1 domestic,
#1 Romania, #2 domestic, ...), one overall limit on the films actually added
(#139), the minimum-gross gate on the film's best weekend in any region,
each TMDB film considered once per run, and a pass that stops when Radarr
stops answering.
"""

import httpx
import pytest

from src.core import auto_add
from src.core.auto_add import auto_add_missing_movies
from src.core.boxoffice import BoxOfficeMovie
from src.core.exceptions import (
    RadarrConnectionError,
    RadarrError,
    RadarrNotFoundError,
)
from src.core.matcher import MatchResult
from src.core.region_charts import RegionFetch, merge_region_charts
from src.utils.config import Settings


def movie(rank, title, gross=None, imdb=None, region=""):
    return BoxOfficeMovie(
        rank=rank, title=title, weekend_gross=gross, imdb_id=imdb, region=region
    )


class _FakeQualityProfile:
    id = 1
    name = "HD-1080p"


class _FakeAddedMovie:
    def __init__(self, title):
        self.title = title


class _FakeIgnoreList:
    ignored_titles: set = set()

    def get_ignored_tmdb_ids(self):
        return {_tmdb_id(title) for title in self.ignored_titles}


def _tmdb_id(title):
    return 1000 + sum(map(ord, title))


class _FakeRootFolderManager:
    def __init__(self, *_, **__):
        pass

    def determine_root_folder(self, genres=None, movie_title=None):
        return "/movies"


class _FakeRadarrService:
    """Looks every title up as its own film unless ``same_film`` says otherwise."""

    def __init__(self, same_film=None, not_on_tmdb=(), rejected=(), lookup_errors=None):
        self.same_film = same_film or {}
        self.not_on_tmdb = set(not_on_tmdb)
        # A set of titles Radarr rejects, or title -> the exception it raises
        self.rejected = rejected if isinstance(rejected, dict) else set(rejected)
        # title -> exception the lookup raises, or one exception for every title
        self.lookup_errors = lookup_errors or {}
        self.searched = []
        self.added = []
        self._titles = {}

    def get_quality_profiles(self):
        return [_FakeQualityProfile()]

    def get_languages(self):
        return ["English"]

    def search_movie(self, title):
        self.searched.append(title)
        if isinstance(self.lookup_errors, BaseException):
            raise self.lookup_errors
        if title in self.lookup_errors:
            raise self.lookup_errors[title]
        if title in self.not_on_tmdb:
            return []
        canonical = self.same_film.get(title, title)
        tmdb_id = _tmdb_id(canonical)
        self._titles[tmdb_id] = canonical
        return [{"tmdbId": tmdb_id, "title": canonical, "year": 2026, "genres": []}]

    def add_movie(self, tmdb_id, profile_id, root_folder, monitored, search):
        title = self._titles[tmdb_id]
        if isinstance(self.rejected, dict) and title in self.rejected:
            raise self.rejected[title]
        if title in self.rejected:
            # Radarr's answer for a film it already has.
            raise RuntimeError("400 Bad Request: This movie has already been added")
        self.added.append(self._titles[tmdb_id])
        return _FakeAddedMovie(self._titles[tmdb_id])


def _settings(**overrides):
    values = {"radarr_api_key": "test", "boxarr_features_auto_add_limit": 10}
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(auto_add, "IgnoreList", _FakeIgnoreList)
    monkeypatch.setattr(_FakeIgnoreList, "ignored_titles", set())
    monkeypatch.setattr(auto_add, "RootFolderManager", _FakeRootFolderManager)

    def _run(config, films, regions, service=None):
        monkeypatch.setattr(auto_add, "settings", config)
        service = service or _FakeRadarrService()
        results = [MatchResult(box_office_movie=film) for film in films]
        added = auto_add_missing_movies(results, service, 2026, regions=regions)
        return added, service

    return _run


def _merged(*charts):
    """Merge (region, [movies]) charts in configured order."""
    regions = [region for region, _ in charts]
    fetches = [
        RegionFetch(region=region, status="ok", movies=rows) for region, rows in charts
    ]
    return merge_region_charts(fetches, regions), regions


class TestChartOrderAndLimit:
    def test_regions_are_interleaved_by_rank(self, run):
        films, regions = _merged(
            ("", [movie(1, "D1", region=""), movie(2, "D2", region="")]),
            ("RO", [movie(1, "R1", region="RO"), movie(2, "R2", region="RO")]),
        )
        # Whatever order the results arrive in.
        added, service = run(_settings(), list(reversed(films)), regions)
        assert service.searched == ["D1", "R1", "D2", "R2"]
        assert added == ["D1", "R1", "D2", "R2"]

    def test_the_limit_is_one_cap_for_the_whole_run(self, run):
        films, regions = _merged(
            ("", [movie(1, "D1"), movie(2, "D2")]),
            ("RO", [movie(1, "R1"), movie(2, "R2")]),
        )
        added, _ = run(_settings(boxarr_features_auto_add_limit=3), films, regions)
        assert added == ["D1", "R1", "D2"]

    def test_a_film_a_gate_drops_does_not_use_a_slot(self, run):
        # #139: the limit counts films added, not chart rows looked at.
        films, regions = _merged(
            ("", [movie(1, "Small", 10_000.0)]),
            ("RO", [movie(1, "Big", 9_000_000.0)]),
        )
        added, service = run(
            _settings(
                boxarr_features_auto_add_limit=1,
                boxarr_features_auto_add_min_gross_enabled=True,
                boxarr_features_auto_add_min_gross=1_000_000.0,
            ),
            films,
            regions,
        )
        assert added == ["Big"]
        assert service.searched == ["Big"]

    def test_an_ignored_film_does_not_use_a_slot(self, run, monkeypatch):
        # The reporter's case: limit 1 and an ignored #1 added nothing.
        monkeypatch.setattr(_FakeIgnoreList, "ignored_titles", {"A"})
        added, _ = run(
            _settings(boxarr_features_auto_add_limit=1),
            [movie(1, "A"), movie(2, "B"), movie(3, "C")],
            [""],
        )
        assert added == ["B"]

    def test_a_film_missing_from_tmdb_does_not_use_a_slot(self, run):
        service = _FakeRadarrService(not_on_tmdb={"A"})
        added, _ = run(
            _settings(boxarr_features_auto_add_limit=1),
            [movie(1, "A"), movie(2, "B")],
            [""],
            service,
        )
        assert added == ["B"]

    def test_a_film_radarr_rejects_does_not_use_a_slot(self, run):
        # E.g. already in Radarr under a title the matcher did not link.
        service = _FakeRadarrService(rejected={"A"})
        added, _ = run(
            _settings(boxarr_features_auto_add_limit=1),
            [movie(1, "A"), movie(2, "B"), movie(3, "C")],
            [""],
            service,
        )
        assert added == ["B"]
        assert service.added == ["B"]

    def test_a_tmdb_film_seen_twice_uses_one_slot(self, run):
        films, regions = _merged(
            ("", [movie(1, "The Housemaid"), movie(2, "Other")]),
            ("DE", [movie(1, "Die Haushaelterin")]),
        )
        service = _FakeRadarrService(same_film={"Die Haushaelterin": "The Housemaid"})
        added, _ = run(
            _settings(boxarr_features_auto_add_limit=2), films, regions, service
        )
        assert added == ["The Housemaid", "Other"]

    def test_the_run_stops_once_the_limit_is_added(self, run, caplog):
        with caplog.at_level("INFO"):
            added, service = run(
                _settings(boxarr_features_auto_add_limit=2),
                [movie(1, "A"), movie(2, "B"), movie(3, "C"), movie(4, "D")],
                [""],
            )
        assert added == ["A", "B"]
        # No Radarr lookups past the limit.
        assert service.searched == ["A", "B"]
        assert any(
            "limit of 2" in r.message and "2 more" in r.message for r in caplog.records
        )

    def test_one_region_keeps_rank_order(self, run):
        films = [movie(1, "A"), movie(2, "B"), movie(3, "C")]
        added, _ = run(_settings(boxarr_features_auto_add_limit=2), films, [""])
        assert added == ["A", "B"]


def _radarr_error(cause):
    """A RadarrError the way RadarrService._make_request raises it."""
    error = RadarrError(f"Radarr API error: {cause}")
    error.__cause__ = cause
    return error


def _http_status(status):
    request = httpx.Request("GET", "http://radarr:7878/api/v3/movie/lookup")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def _timeout():
    request = httpx.Request("GET", "http://radarr:7878/api/v3/movie/lookup")
    return httpx.ReadTimeout("timed out", request=request)


class TestRadarrUnavailable:
    """The limit counts only films added, so an outage must end the pass."""

    @pytest.mark.parametrize(
        "error",
        [
            _radarr_error(_timeout()),
            _radarr_error(_http_status(503)),
            RadarrConnectionError("Cannot connect to Radarr"),
            _timeout(),
        ],
        ids=["timeout", "503", "connection", "raw-httpx-timeout"],
    )
    def test_a_radarr_outage_stops_the_pass(self, run, caplog, error):
        films = [movie(rank, f"Film {rank}") for rank in range(1, 21)]
        service = _FakeRadarrService(lookup_errors=error)
        with caplog.at_level("INFO"):
            added, _ = run(_settings(), films, [""], service)
        assert added == []
        # Every lookup waits out radarr_timeout; three tell Radarr is down.
        assert service.searched == ["Film 1", "Film 2", "Film 3"]
        assert any(
            "3 times in a row" in r.message and "17 more" in r.message
            for r in caplog.records
        )

    def test_an_add_outage_stops_the_pass(self, run, caplog):
        # Lookups answer, but every add times out: each film still waits
        # out radarr_timeout, so the failed adds must end the pass too.
        down = _radarr_error(_timeout())
        titles = [f"Film {rank}" for rank in range(1, 11)]
        service = _FakeRadarrService(rejected={title: down for title in titles})
        with caplog.at_level("INFO"):
            added, _ = run(
                _settings(),
                [movie(rank, title) for rank, title in enumerate(titles, 1)],
                [""],
                service,
            )
        assert added == []
        assert service.searched == titles[:3]
        assert any(
            "3 times in a row" in r.message and "7 more" in r.message
            for r in caplog.records
        )

    def test_rejections_do_not_stop_the_pass(self, run):
        # A 4xx is Radarr answering about one film, however many in a row.
        service = _FakeRadarrService(
            lookup_errors={
                "A": _radarr_error(_http_status(400)),
                "B": RadarrNotFoundError("Resource not found: /api/v3/movie/lookup"),
                "C": _radarr_error(_http_status(422)),
                "D": _radarr_error(_http_status(400)),
            }
        )
        added, _ = run(
            _settings(boxarr_features_auto_add_limit=1),
            [movie(rank, title) for rank, title in enumerate("ABCDE", 1)],
            [""],
            service,
        )
        assert added == ["E"]
        assert service.searched == list("ABCDE")

    def test_films_radarr_refuses_to_add_do_not_stop_the_pass(self, run):
        rejected = {title: _radarr_error(_http_status(400)) for title in "ABCD"}
        service = _FakeRadarrService(rejected=rejected)
        added, _ = run(
            _settings(boxarr_features_auto_add_limit=1),
            [movie(rank, title) for rank, title in enumerate("ABCDE", 1)],
            [""],
            service,
        )
        assert added == ["E"]

    def test_failures_between_answers_do_not_stop_the_pass(self, run):
        down = _radarr_error(_timeout())
        service = _FakeRadarrService(
            lookup_errors={"A": down, "B": down, "D": down, "E": down}
        )
        added, _ = run(
            _settings(),
            [movie(rank, title) for rank, title in enumerate("ABCDEF", 1)],
            [""],
            service,
        )
        assert added == ["C", "F"]
        assert service.searched == list("ABCDEF")


class TestBestGross:
    def config(self):
        return _settings(
            boxarr_features_auto_add_min_gross_enabled=True,
            boxarr_features_auto_add_min_gross=2_000_000.0,
        )

    def test_best_weekend_in_any_region_passes_the_gate(self, run):
        # Clayface in 2026W44: $11.0M domestic, $60.8K in Romania, where it
        # ranked higher - the lead row is the Romanian one.
        films, regions = _merged(
            ("", [movie(5, "Clayface", 11_000_000.0, "tt34890576")]),
            ("RO", [movie(2, "Clayface", 60_800.0, "tt34890576")]),
        )
        assert films[0].region == "RO"
        added, _ = run(self.config(), films, regions)
        assert added == ["Clayface"]

    def test_small_everywhere_is_skipped(self, run, caplog):
        films, regions = _merged(
            ("", [movie(9, "Tiny", 100_000.0, "tt1")]),
            ("RO", [movie(3, "Tiny", 50_000.0, "tt1")]),
        )
        with caplog.at_level("INFO"):
            added, service = run(self.config(), films, regions)
        assert added == []
        assert service.searched == []
        skip = [r.message for r in caplog.records if "Tiny" in r.message][0]
        assert "(rank #3 RO)" in skip
        assert "$100,000 below minimum $2,000,000" in skip

    def test_unknown_gross_in_every_region_still_passes(self, run):
        films, regions = _merged(
            ("", [movie(1, "Unknown", None, "tt1")]),
            ("RO", [movie(1, "Unknown", None, "tt1")]),
        )
        added, _ = run(self.config(), films, regions)
        assert added == ["Unknown"]

    def test_a_plain_chart_row_uses_its_own_gross(self, run):
        added, _ = run(
            self.config(),
            [movie(1, "Big", 3_000_000.0), movie(2, "Small", 10.0)],
            [""],
        )
        assert added == ["Big"]


class TestDuplicateGuard:
    def test_a_tmdb_film_is_considered_once_per_run(self, run, caplog):
        # Different titles, no IMDb ids: the charts could not merge them,
        # but both look up the same TMDB film.
        films, regions = _merged(
            ("", [movie(1, "The Housemaid")]),
            ("DE", [movie(1, "Die Haushaelterin")]),
        )
        assert len(films) == 2
        service = _FakeRadarrService(same_film={"Die Haushaelterin": "The Housemaid"})
        with caplog.at_level("INFO"):
            added, _ = run(_settings(), films, regions, service)
        assert added == ["The Housemaid"]
        assert service.added == ["The Housemaid"]
        notes = [r.message for r in caplog.records if "already considered" in r.message]
        assert len(notes) == 1
        assert "Die Haushaelterin" in notes[0]
        assert "rank #1 DE" in notes[0]
