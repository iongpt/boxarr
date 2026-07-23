"""Unit tests for IMDb-aware search result selection in auto-add."""

from src.core.auto_add import _select_search_result
from src.core.boxoffice import BoxOfficeMovie


def _results():
    """Three Radarr search results with distinct IMDb ids."""
    return [
        {"tmdbId": 10, "title": "Ambiguous Title (2001)", "imdbId": "tt0000001"},
        {"tmdbId": 20, "title": "Ambiguous Title (2015)", "imdbId": "tt0000002"},
        {"tmdbId": 30, "title": "Ambiguous Title (2024)", "imdbId": "tt0000003"},
    ]


def test_imdb_match_prefers_matching_result_over_first():
    """When the box-office movie carries an IMDb id, the matching result wins."""
    movie = BoxOfficeMovie(rank=1, title="Ambiguous Title", imdb_id="tt0000003")
    selected = _select_search_result(_results(), movie)
    assert selected["tmdbId"] == 30


def test_imdb_match_is_case_insensitive():
    """IMDb id comparison normalizes case and surrounding whitespace."""
    movie = BoxOfficeMovie(rank=1, title="Ambiguous Title", imdb_id="  TT0000002 ")
    selected = _select_search_result(_results(), movie)
    assert selected["tmdbId"] == 20


def test_no_imdb_id_falls_back_to_first_result():
    """Without an IMDb id, behavior is identical to taking the first result."""
    movie = BoxOfficeMovie(rank=1, title="Ambiguous Title", imdb_id=None)
    selected = _select_search_result(_results(), movie)
    assert selected["tmdbId"] == 10


def test_no_match_falls_back_to_first_and_warns(caplog):
    """An unmatched IMDb id falls back to result[0] and logs a warning."""
    movie = BoxOfficeMovie(rank=1, title="Ambiguous Title", imdb_id="tt9999999")
    with caplog.at_level("WARNING"):
        selected = _select_search_result(_results(), movie)
    assert selected["tmdbId"] == 10
    assert any("tt9999999" in rec.message for rec in caplog.records)
    assert any("Ambiguous Title" in rec.message for rec in caplog.records)
