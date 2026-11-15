"""The shared TMDB pick: the lookup result that is the charted film (#141).

A title search can list several films of one title. The pick goes by Box
Office Mojo's IMDb id: the result carrying it, else Radarr's ``imdb:<id>``
lookup, else the first result that does not name another film, and only
when every result names another film, the first result.
"""

import pytest

from src.core.boxoffice import BoxOfficeMovie
from src.core.tmdb_pick import pick_tmdb_film, resolve_tmdb_pick

SUPERMAN_1978 = {
    "tmdbId": 1924,
    "title": "Superman",
    "year": 1978,
    "imdbId": "tt0078346",
}
SUPERMAN_2025 = {
    "tmdbId": 1061474,
    "title": "Superman",
    "year": 2025,
    "imdbId": "tt5950044",
}
LILO_2002 = {
    "tmdbId": 11544,
    "title": "Lilo & Stitch",
    "year": 2002,
    "imdbId": "tt0275847",
}
# Listed without an IMDb id, as TMDB can for a new film.
LILO_2025 = {"tmdbId": 552524, "title": "Lilo & Stitch", "year": 2025}


class _Lookup:
    """Radarr's movie lookup: term -> results; an exception is raised."""

    def __init__(self, results):
        self.results = results
        self.terms = []

    def search_movie(self, term):
        self.terms.append(term)
        found = self.results.get(term, [])
        if isinstance(found, BaseException):
            raise found
        return found


def _ambiguous():
    """Three Radarr search results with distinct IMDb ids."""
    return {
        "Ambiguous Title": [
            {"tmdbId": 10, "title": "Ambiguous Title", "imdbId": "tt0000001"},
            {"tmdbId": 20, "title": "Ambiguous Title", "imdbId": "tt0000002"},
            {"tmdbId": 30, "title": "Ambiguous Title", "imdbId": "tt0000003"},
        ]
    }


def _chart(title, imdb_id=None):
    return BoxOfficeMovie(rank=1, title=title, imdb_id=imdb_id)


class TestPickByImdbId:
    def test_result_with_the_mojo_imdb_id_wins_over_the_first(self):
        lookup = _Lookup(_ambiguous())

        picked = resolve_tmdb_pick(lookup, _chart("Ambiguous Title", "tt0000003"))

        assert picked["tmdbId"] == 30
        assert lookup.terms == ["Ambiguous Title"]

    def test_imdb_id_comparison_ignores_case_and_whitespace(self):
        picked = resolve_tmdb_pick(
            _Lookup(_ambiguous()), _chart("Ambiguous Title", "  TT0000002 ")
        )

        assert picked["tmdbId"] == 20

    def test_without_an_imdb_id_the_first_result_is_taken(self):
        lookup = _Lookup(_ambiguous())

        picked = resolve_tmdb_pick(lookup, _chart("Ambiguous Title"))

        assert picked["tmdbId"] == 10
        assert lookup.terms == ["Ambiguous Title"]

    def test_nothing_found_is_none(self):
        assert resolve_tmdb_pick(_Lookup({}), _chart("Nothing")) is None


class TestImdbLookup:
    def test_film_missing_from_the_title_search_is_found_by_its_imdb_id(self):
        lookup = _Lookup(
            {"Superman": [SUPERMAN_1978], "imdb:tt5950044": [SUPERMAN_2025]}
        )

        picked = resolve_tmdb_pick(lookup, _chart("Superman", "tt5950044"))

        assert picked == SUPERMAN_2025
        assert lookup.terms == ["Superman", "imdb:tt5950044"]

    def test_title_search_finding_nothing_falls_back_to_the_imdb_lookup(self):
        """A localized regional title TMDB does not know."""
        lookup = _Lookup({"imdb:tt5950044": [SUPERMAN_2025]})

        picked = resolve_tmdb_pick(lookup, _chart("Superman - Der Film", "tt5950044"))

        assert picked == SUPERMAN_2025

    def test_imdb_lookup_returning_another_film_is_not_taken(self):
        lookup = _Lookup(
            {"Superman": [SUPERMAN_1978], "imdb:tt5950044": [SUPERMAN_1978]}
        )

        picked = resolve_tmdb_pick(lookup, _chart("Superman", "tt5950044"))

        # Every result names another film: the first title result, as before.
        assert picked == SUPERMAN_1978

    def test_failing_imdb_lookup_falls_back_to_the_title_results(self, caplog):
        lookup = _Lookup(
            {"Superman": [SUPERMAN_1978], "imdb:tt5950044": RuntimeError("boom")}
        )

        with caplog.at_level("WARNING"):
            picked = resolve_tmdb_pick(lookup, _chart("Superman", "tt5950044"))

        assert picked == SUPERMAN_1978
        assert any("boom" in rec.message for rec in caplog.records)

    def test_failing_title_search_is_raised(self):
        lookup = _Lookup({"Superman": RuntimeError("Radarr is down")})

        with pytest.raises(RuntimeError, match="Radarr is down"):
            resolve_tmdb_pick(lookup, _chart("Superman", "tt5950044"))

    def test_no_imdb_lookup_when_the_title_search_has_the_film(self):
        lookup = _Lookup({"Superman": [SUPERMAN_1978, SUPERMAN_2025]})

        assert resolve_tmdb_pick(lookup, _chart("Superman", "tt5950044")) == (
            SUPERMAN_2025
        )
        assert lookup.terms == ["Superman"]


class TestFallback:
    def test_result_naming_another_film_is_skipped(self):
        """The 2025 film is listed without an IMDb id, after the 2002 one."""
        lookup = _Lookup({"Lilo & Stitch": [LILO_2002, LILO_2025]})

        picked = resolve_tmdb_pick(lookup, _chart("Lilo & Stitch", "tt11655566"))

        assert picked == LILO_2025
        assert lookup.terms == ["Lilo & Stitch", "imdb:tt11655566"]

    def test_every_result_naming_another_film_falls_back_to_the_first(self, caplog):
        with caplog.at_level("WARNING"):
            picked = resolve_tmdb_pick(
                _Lookup(_ambiguous()), _chart("Ambiguous Title", "tt9999999")
            )

        assert picked["tmdbId"] == 10
        assert any("tt9999999" in rec.message for rec in caplog.records)
        assert any("Ambiguous Title" in rec.message for rec in caplog.records)

    def test_a_given_year_is_preferred(self):
        lookup = _Lookup({"Superman": [SUPERMAN_1978, SUPERMAN_2025]})

        assert pick_tmdb_film(lookup, "Superman", year=2025) == SUPERMAN_2025
        assert pick_tmdb_film(lookup, "Superman", year=1978) == SUPERMAN_1978
        assert pick_tmdb_film(lookup, "Superman", year=1999) == SUPERMAN_1978

    def test_the_imdb_id_wins_over_the_year(self):
        lookup = _Lookup({"Superman": [SUPERMAN_1978, SUPERMAN_2025]})

        assert (
            pick_tmdb_film(lookup, "Superman", "tt0078346", year=2025) == SUPERMAN_1978
        )
