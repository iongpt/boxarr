"""Unit tests for the auto-add minimum weekend-gross filter (issue #124).

The threshold is compared against the *weekend* gross in US dollars - the
figure the Box Office Mojo chart is ranked by, and the only one reported in a
single currency for every region. These tests call the production predicate
(``src.core.auto_add._gross_allowed``) and the auto-add loop itself, so a
filter that stops being wired into the loop fails here.
"""

import pytest
import yaml
from fastapi.testclient import TestClient

import src.api.routes.config as cfg_routes
import src.utils.config as cfg_utils
from src.api.app import create_app
from src.core import auto_add
from src.core.auto_add import _gross_allowed, auto_add_missing_movies
from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MatchResult
from src.utils.config import Settings


def _settings(**overrides):
    """Build a Settings object with the minimum-gross filter enabled."""
    values = {
        "radarr_api_key": "test",
        "boxarr_features_auto_add_min_gross_enabled": True,
        "boxarr_features_auto_add_min_gross": 2_000_000.0,
    }
    values.update(overrides)
    return Settings(**values)


class TestMinGrossDefaults:
    """Config defaults: the filter must be inert until a user turns it on."""

    def test_default_disabled(self):
        """The minimum-gross filter is disabled by default."""
        s = Settings(radarr_api_key="test")
        assert s.boxarr_features_auto_add_min_gross_enabled is False

    def test_default_threshold_is_zero(self):
        """The default threshold is 0 - no movie is filtered out."""
        s = Settings(radarr_api_key="test")
        assert s.boxarr_features_auto_add_min_gross == 0.0

    def test_negative_threshold_rejected(self):
        """A negative threshold is meaningless and is refused by the schema."""
        with pytest.raises(Exception):
            Settings(radarr_api_key="test", boxarr_features_auto_add_min_gross=-1000.0)


class TestGrossAllowed:
    """Semantics of the predicate the loop uses."""

    def test_gross_above_threshold_passes(self):
        assert _gross_allowed(5_000_000.0, _settings()) is True

    def test_gross_equal_to_threshold_passes(self):
        """The threshold is a minimum, so an exact match is allowed."""
        assert _gross_allowed(2_000_000.0, _settings()) is True

    def test_gross_below_threshold_is_skipped(self):
        assert _gross_allowed(1_999_999.0, _settings()) is False

    def test_unknown_gross_fails_open(self):
        """An unparsed gross means the chart broke - never disable auto-add."""
        assert _gross_allowed(None, _settings()) is True

    def test_disabled_filter_lets_everything_through(self):
        s = _settings(boxarr_features_auto_add_min_gross_enabled=False)
        assert _gross_allowed(1.0, s) is True

    def test_zero_threshold_is_inert_even_when_enabled(self):
        """Enabled with a 0 threshold filters nothing (nothing grosses < 0)."""
        s = _settings(boxarr_features_auto_add_min_gross=0.0)
        assert _gross_allowed(0.0, s) is True
        assert _gross_allowed(None, s) is True


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


class _FakeRadarrService:
    """Radarr double that records every lookup the loop performs."""

    def __init__(self):
        self.added = []
        self.searched = []
        self._tmdb_ids = {}

    def get_quality_profiles(self):
        return [_FakeQualityProfile()]

    def get_languages(self):
        return ["English"]

    def search_movie(self, title):
        self.searched.append(title)
        tmdb_id = len(self._tmdb_ids) + 1
        self._tmdb_ids[tmdb_id] = title
        return [
            {
                "tmdbId": tmdb_id,
                "title": title,
                "year": 2025,
                "genres": ["Action"],
                "originalLanguage": {"id": 1, "name": "English"},
            }
        ]

    def add_movie(self, tmdb_id, profile_id, root_folder, monitored, search):
        title = self._tmdb_ids[tmdb_id]
        self.added.append(title)
        return _FakeAddedMovie(title)


# (rank, title, weekend gross in USD)
_CHART = [
    (1, "Blockbuster", 5_000_000.0),
    (2, "Indie Darling", 500_000.0),
    (3, "Mystery Release", None),
]


@pytest.fixture
def auto_add_env(monkeypatch):
    """Run auto_add_missing_movies without touching the filesystem or Radarr."""
    monkeypatch.setattr(auto_add, "IgnoreList", _FakeIgnoreList)
    monkeypatch.setattr(auto_add, "RootFolderManager", _FakeRootFolderManager)

    def _run(config, chart=_CHART):
        monkeypatch.setattr(auto_add, "settings", config)
        service = _FakeRadarrService()
        results = [
            MatchResult(
                box_office_movie=BoxOfficeMovie(
                    rank=rank, title=title, weekend_gross=gross
                )
            )
            for rank, title, gross in chart
        ]
        return auto_add_missing_movies(results, service, 2025), service

    return _run


class TestMinGrossInAutoAddLoop:
    """The loop must apply the filter - not just the extracted helper."""

    def test_below_threshold_movie_is_not_added(self, auto_add_env):
        added, _ = auto_add_env(_settings())
        assert added == ["Blockbuster", "Mystery Release"]

    def test_below_threshold_movie_costs_no_radarr_lookup(self, auto_add_env):
        """The gate runs before search_movie, so a skip is free."""
        _, service = auto_add_env(_settings())
        assert service.searched == ["Blockbuster", "Mystery Release"]
        assert "Indie Darling" not in service.searched

    def test_disabled_filter_adds_everything(self, auto_add_env):
        added, service = auto_add_env(
            _settings(boxarr_features_auto_add_min_gross_enabled=False)
        )
        assert added == ["Blockbuster", "Indie Darling", "Mystery Release"]
        assert len(service.searched) == 3

    def test_unknown_gross_is_added_and_logged(self, auto_add_env, caplog):
        with caplog.at_level("INFO"):
            added, _ = auto_add_env(_settings())
        assert "Mystery Release" in added
        assert any(
            "Mystery Release" in record.message
            and "unknown weekend gross" in record.message
            for record in caplog.records
        )

    def test_unknown_gross_is_not_logged_when_the_filter_is_off(
        self, auto_add_env, caplog
    ):
        """The 'adding anyway' note is about the filter - stay quiet when it is off."""
        with caplog.at_level("INFO"):
            auto_add_env(_settings(boxarr_features_auto_add_min_gross_enabled=False))
        assert not any(
            "unknown weekend gross" in record.message for record in caplog.records
        )

    def test_skip_is_logged_with_formatted_amounts(self, auto_add_env, caplog):
        with caplog.at_level("INFO"):
            auto_add_env(_settings())
        skips = [
            record.message
            for record in caplog.records
            if "Indie Darling" in record.message
        ]
        assert skips, "the skipped movie must be logged"
        assert "$500,000" in skips[0]
        assert "$2,000,000" in skips[0]

    def test_limit_is_applied_before_the_gross_gate(self, auto_add_env):
        """'Maximum Movies to Add' keeps meaning 'the top X by rank'.

        Rank #1 is below the threshold and rank #2 is above it. With a limit
        of 1 the limit trims to rank #1 first, which the gross gate then
        rejects - nothing is added. Were the gate applied first, rank #2 would
        survive the trim and be added.
        """
        added, service = auto_add_env(
            _settings(boxarr_features_auto_add_limit=1),
            chart=[(1, "Small Opener", 100_000.0), (2, "Big Opener", 9_000_000.0)],
        )
        assert added == []
        assert service.searched == []


class TestMinGrossYamlRoundTrip:
    """The generic auto_add_options loader must pick the new keys up."""

    def test_round_trip_via_auto_add_options(self, tmp_path):
        config_path = tmp_path / "local.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "boxarr": {
                        "features": {
                            "auto_add_options": {
                                "min_gross_enabled": True,
                                "min_gross": 2_500_000,
                            }
                        }
                    }
                }
            )
        )

        s = Settings(radarr_api_key="test")
        s.load_from_yaml(config_path)

        assert s.boxarr_features_auto_add_min_gross_enabled is True
        assert s.boxarr_features_auto_add_min_gross == 2_500_000


class _FakeRadarrConnection:
    def __init__(self, *_, **__):
        pass

    def test_connection(self) -> bool:
        return True


def _base_payload() -> dict:
    return {
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "test-key",
        "radarr_root_folder": "/movies",
        "radarr_quality_profile_default": "HD-1080p",
        "radarr_quality_profile_upgrade": "",
        "boxarr_scheduler_enabled": False,
        "boxarr_scheduler_cron": "0 23 * * 2",
        "boxarr_features_auto_add": True,
        "boxarr_features_quality_upgrade": True,
        "boxarr_ui_theme": "light",
    }


def _seed(tmp_path, **auto_add_options) -> None:
    with open(tmp_path / "local.yaml", "w") as f:
        yaml.safe_dump(
            {
                "radarr": {"api_key": "test-key"},
                "boxarr": {"features": {"auto_add_options": auto_add_options}},
            },
            f,
        )


def _saved_options(tmp_path) -> dict:
    with open(tmp_path / "local.yaml") as f:
        config = yaml.safe_load(f) or {}
    return config["boxarr"]["features"]["auto_add_options"]


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_routes, "RadarrService", _FakeRadarrConnection)
    # monkeypatch, not a bare assignment: the settings built from this (soon
    # deleted) tmp_path must not leak into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


class TestMinGrossSaveHandler:
    """A save must persist the threshold - and never silently zero it."""

    def test_posted_threshold_is_persisted(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        payload = _base_payload()
        payload["boxarr_features_auto_add_min_gross_enabled"] = True
        payload["boxarr_features_auto_add_min_gross"] = 3_500_000

        resp = client.post("/api/config/save", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("success") is True

        options = _saved_options(tmp_path)
        assert options["min_gross_enabled"] is True
        assert options["min_gross"] == 3_500_000

    def test_omitted_threshold_is_carried_over(self, tmp_path, monkeypatch):
        """A client that never learned the field cannot reset it to 0.

        Only the amount carries over. The enabled flag is a plain bool, like
        every other filter flag, so an omitting save falls back to its default
        (off) - pinned here so the carry-over is not read as more than it is.
        """
        _seed(tmp_path, min_gross_enabled=True, min_gross=2_000_000)
        client = _client(tmp_path, monkeypatch)

        payload = _base_payload()  # No min_gross key at all.
        resp = client.post("/api/config/save", json=payload)
        assert resp.status_code == 200

        options = _saved_options(tmp_path)
        assert options["min_gross"] == 2_000_000
        assert options["min_gross_enabled"] is False

    def test_threshold_can_be_set_back_to_zero(self, tmp_path, monkeypatch):
        """An explicit 0 is honored - carry-over only covers a missing field."""
        _seed(tmp_path, min_gross_enabled=True, min_gross=2_000_000)
        client = _client(tmp_path, monkeypatch)

        payload = _base_payload()
        payload["boxarr_features_auto_add_min_gross_enabled"] = False
        payload["boxarr_features_auto_add_min_gross"] = 0

        resp = client.post("/api/config/save", json=payload)
        assert resp.status_code == 200

        options = _saved_options(tmp_path)
        assert options["min_gross"] == 0
        assert options["min_gross_enabled"] is False

    def test_negative_threshold_is_rejected(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)

        payload = _base_payload()
        payload["boxarr_features_auto_add_min_gross"] = -1

        resp = client.post("/api/config/save", json=payload)
        assert resp.status_code == 422
