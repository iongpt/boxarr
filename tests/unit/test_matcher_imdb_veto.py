"""Regression tests for #135: a differing IMDb id vetoes a title match.

A new sequel, spin-off or remake is not in Radarr yet, so the IMDb fast path
misses and the matcher falls back to title matching. The permissive title
fallbacks (base title cut at ':' or '-', title-only exact index) used to link
the chart entry to the OLDER film already in the library, so the card showed
"Downloaded" and auto-add skipped the new film. When both sides carry an IMDb
id and the ids differ they are different films, and the title match must be
rejected. Title matching still applies when either id is missing.

Titles, years and IMDb ids below are the real ones.
"""

import pytest

from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MatchResult, MovieMatcher
from src.core.radarr import RadarrMovie

_next_id = iter(range(1, 10_000))


def _lib(title, year, imdb_id):
    rid = next(_next_id)
    return RadarrMovie(
        id=rid, title=title, year=year, tmdbId=100_000 + rid, imdbId=imdb_id
    )


def _chart(title, imdb_id, rank=1):
    return BoxOfficeMovie(rank=rank, title=title, imdb_id=imdb_id)


def _match_batch(matcher, box_movie, library):
    return matcher.match_batch([box_movie], library)[0]


def _match_movies(matcher, box_movie, library):
    return matcher.match_movies([box_movie], library)[0]


def _match_movie(matcher, box_movie, library):
    return matcher.match_movie(box_movie, library)


ENTRY_POINTS = pytest.mark.parametrize(
    "match",
    [_match_batch, _match_movies, _match_movie],
    ids=["match_batch", "match_movies", "match_movie"],
)


# (chart title, chart IMDb id, older library film (title, year, IMDb id))
NEW_FILM_VS_OLDER_LIBRARY_FILM = [
    pytest.param(
        "Wicked: For Good",
        "tt19847976",
        ("Wicked", 2024, "tt1262426"),
        id="wicked-for-good-vs-wicked",
    ),
    pytest.param(
        "Spider-Man: Brand New Day",
        "tt22084616",
        ("Spider-Man: No Way Home", 2021, "tt10872600"),
        id="spider-man-brand-new-day-vs-no-way-home",
    ),
    pytest.param(
        "Spider-Man: Brand New Day",
        "tt22084616",
        ("Spider-Man", 2002, "tt0145487"),
        id="spider-man-brand-new-day-vs-spider-man-2002",
    ),
    pytest.param(
        "Lilo & Stitch",
        "tt11655566",
        ("Lilo & Stitch", 2002, "tt0275847"),
        id="lilo-and-stitch-remake-vs-2002",
    ),
    pytest.param(
        "Superman",
        "tt5950044",
        ("Superman", 1978, "tt0078346"),
        id="superman-2025-vs-1978",
    ),
    pytest.param(
        "Smurfs",
        "tt23251300",
        ("The Smurfs", 2011, "tt0472181"),
        id="smurfs-vs-the-smurfs",
    ),
    pytest.param(
        "Mission: Impossible - The Final Reckoning",
        "tt9603208",
        ("Mission: Impossible", 1996, "tt0117060"),
        id="mission-impossible-final-reckoning-vs-1996",
    ),
    pytest.param(
        "Avatar: Fire and Ash",
        "tt1757678",
        ("Avatar", 2009, "tt0499549"),
        id="avatar-fire-and-ash-vs-avatar",
    ),
]


class TestDifferentImdbIdVetoesTitleMatch:
    """The reported defect: a title match across two IMDb ids is rejected."""

    @ENTRY_POINTS
    @pytest.mark.parametrize(
        "chart_title, chart_imdb, older", NEW_FILM_VS_OLDER_LIBRARY_FILM
    )
    def test_new_film_does_not_match_older_library_film(
        self, match, chart_title, chart_imdb, older
    ):
        library = [_lib(*older)]
        box_movie = _chart(chart_title, chart_imdb)

        result = match(MovieMatcher(), box_movie, library)

        assert not result.is_matched
        assert result.radarr_movie is None
        assert result.match_method == "none"
        assert result.confidence == 0.0
        # Auto-add reads the chart entry (and its IMDb id) off the result.
        assert result.box_office_movie is box_movie

    @ENTRY_POINTS
    def test_veto_compares_ids_case_and_whitespace_insensitively(self, match):
        """Ids that differ only in case/whitespace name the same film."""
        library = [_lib("Wicked", 2024, "tt1262426")]
        # Misses the exact-key IMDb index, so it reaches the title fallback.
        box_movie = _chart("Wicked", " TT1262426 ")

        result = match(MovieMatcher(), box_movie, library)

        assert result.is_matched
        assert result.radarr_movie.imdbId == "tt1262426"
        assert result.match_method != "imdb_id"

    def test_batch_keeps_order_and_neighbours_while_vetoing(self):
        library = [
            _lib("Wicked", 2024, "tt1262426"),
            _lib("Superman", 1978, "tt0078346"),
            _lib("Dune: Part Two", 2024, "tt15239678"),
            _lib("Sinners", 2025, None),
            _lib("A Minecraft Movie", 2025, "tt3566834"),
        ]
        chart = [
            _chart("Wicked: For Good", "tt19847976", rank=1),
            _chart("Dune: Part Two", "tt15239678", rank=2),
            _chart("Superman", "tt5950044", rank=3),
            _chart("Sinners", "tt31193180", rank=4),
            _chart("A Minecraft Movie", None, rank=5),
        ]

        results = MovieMatcher().match_batch(chart, library)

        assert [r.box_office_movie for r in results] == chart
        wicked, dune, superman, sinners, minecraft = results
        assert not wicked.is_matched
        assert not superman.is_matched
        assert dune.radarr_movie.title == "Dune: Part Two"
        assert dune.match_method == "imdb_id"
        assert sinners.radarr_movie.title == "Sinners"
        assert sinners.match_method == "exact"
        assert minecraft.radarr_movie.title == "A Minecraft Movie"
        assert minecraft.match_method == "exact"

    @ENTRY_POINTS
    @pytest.mark.parametrize("new_first", [True, False], ids=["new-first", "old-first"])
    def test_links_to_the_new_film_once_both_are_in_the_library(self, match, new_first):
        """After the new film is added, the entry links to it, never the old one."""
        old = _lib("Lilo & Stitch", 2002, "tt0275847")
        new = _lib("Lilo & Stitch", 2025, "tt11655566")
        library = [new, old] if new_first else [old, new]
        box_movie = _chart("Lilo & Stitch", "tt11655566")

        result = match(MovieMatcher(), box_movie, library)

        assert result.radarr_movie is new
        assert result.match_method == "imdb_id"
        assert result.confidence == 1.0


class TestTitleMatchingWhenAnIdIsMissing:
    """Controls: without two ids to compare, title matching is unchanged."""

    @ENTRY_POINTS
    def test_chart_entry_without_imdb_id_still_title_matches(self, match):
        library = [_lib("Wicked", 2024, "tt1262426")]
        box_movie = _chart("Wicked", None)

        result = match(MovieMatcher(), box_movie, library)

        assert result.is_matched
        assert result.radarr_movie.title == "Wicked"
        assert result.match_method == "exact"
        assert result.box_office_movie is box_movie

    @ENTRY_POINTS
    def test_library_film_without_imdb_id_still_title_matches(self, match):
        library = [_lib("Sinners", 2025, None)]
        box_movie = _chart("Sinners", "tt31193180")

        result = match(MovieMatcher(), box_movie, library)

        assert result.is_matched
        assert result.radarr_movie.title == "Sinners"
        assert result.match_method == "exact"

    @ENTRY_POINTS
    def test_blank_library_imdb_id_counts_as_missing(self, match):
        library = [_lib("Sinners", 2025, "  ")]
        box_movie = _chart("Sinners", "tt31193180")

        result = match(MovieMatcher(), box_movie, library)

        assert result.is_matched
        assert result.radarr_movie.title == "Sinners"

    @ENTRY_POINTS
    def test_subtitled_title_match_without_chart_id_still_matches(self, match):
        """Lossy fallbacks still help when the chart has no id to veto with.

        Same film under two titles: the base-title fallback (cut at ':')
        links Box Office Mojo's "F1: The Movie" to Radarr's "F1".
        """
        library = [_lib("F1", 2025, "tt16311594")]
        box_movie = _chart("F1: The Movie", None)

        result = match(MovieMatcher(), box_movie, library)

        assert result.is_matched
        assert result.radarr_movie.title == "F1"
        assert result.match_method == "normalized"


class TestEqualImdbIds:
    """Controls: equal ids are the same film and always match."""

    @ENTRY_POINTS
    def test_equal_ids_match_by_imdb_id(self, match):
        library = [_lib("Wicked", 2024, "tt1262426")]
        box_movie = _chart("Wicked", "tt1262426")

        result = match(MovieMatcher(), box_movie, library)

        assert result.is_matched
        assert result.match_method == "imdb_id"
        assert result.confidence == 1.0

    @ENTRY_POINTS
    def test_equal_ids_match_across_differing_titles(self, match):
        library = [_lib("F1", 2025, "tt16311594")]
        box_movie = _chart("F1: The Movie", "tt16311594")

        result = match(MovieMatcher(), box_movie, library)

        assert result.radarr_movie.title == "F1"
        assert result.match_method == "imdb_id"


def test_vetoed_result_is_a_plain_unmatched_result():
    """A vetoed entry looks exactly like any other unmatched entry."""
    box_movie = _chart("Wicked: For Good", "tt19847976")
    library = [_lib("Wicked", 2024, "tt1262426")]

    result = MovieMatcher().match_movie(box_movie, library)

    assert result == MatchResult(box_office_movie=box_movie)
