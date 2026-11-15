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


class TestOnlyTheConflictingCandidateIsSkipped:
    """The veto skips the film with another IMDb id, not the whole match.

    Radarr can list a new film without an IMDb id (TMDB has not linked one
    yet). A chart entry with Mojo's id must still title-match it, also when
    an older film of the title with an id is in the library too.
    """

    @ENTRY_POINTS
    @pytest.mark.parametrize("old_last", [True, False], ids=["old-last", "old-first"])
    def test_same_title_key_matches_the_film_without_an_id(self, match, old_last):
        old = _lib("Superman", 1978, "tt0078346")
        new = _lib("Superman", 2025, None)
        # The title index keeps the film indexed last for a shared key.
        library = [new, old] if old_last else [old, new]

        result = match(MovieMatcher(), _chart("Superman", "tt5950044"), library)

        assert result.radarr_movie is new
        assert result.match_method == "exact"
        assert result.confidence == 1.0

    @ENTRY_POINTS
    def test_older_film_found_at_an_earlier_stage_is_skipped(self, match):
        """The exact stage finds the 1987 film; the 2025 one, filed without
        the article, is found by the normalized stage."""
        old = _lib("The Running Man", 1987, "tt0093894")
        new = _lib("Running Man", 2025, None)

        result = match(
            MovieMatcher(), _chart("The Running Man", "tt14107334"), [old, new]
        )

        assert result.radarr_movie is new
        assert result.match_method == "normalized"

    @ENTRY_POINTS
    def test_same_id_spelled_differently_further_down_matches(self, match):
        old = _lib("Lilo & Stitch", 2002, "tt0275847")
        new = _lib("Lilo & Stitch", 2025, " TT11655566")

        result = match(
            MovieMatcher(), _chart("Lilo & Stitch", "tt11655566"), [new, old]
        )

        assert result.radarr_movie is new

    @ENTRY_POINTS
    def test_only_films_with_other_ids_is_no_match(self, match):
        library = [
            _lib("Superman", 1978, "tt0078346"),
            _lib("Superman II", 1980, "tt0081573"),
        ]

        result = match(MovieMatcher(), _chart("Superman", "tt5950044"), library)

        assert result == MatchResult(box_office_movie=result.box_office_movie)

    def test_shared_index_is_left_for_the_next_entries(self):
        """A scheduler reuses one matcher: the narrowed search must not
        change what later entries match."""
        old = _lib("Superman", 1978, "tt0078346")
        new = _lib("Superman", 2025, None)
        wicked = _lib("Wicked", 2024, "tt1262426")
        matcher = MovieMatcher()

        first, second, third = matcher.match_batch(
            [
                _chart("Superman", "tt5950044", rank=1),
                _chart("Wicked", None, rank=2),
                _chart("Superman", None, rank=3),
            ],
            [new, old, wicked],
        )

        assert first.radarr_movie is new
        assert second.radarr_movie is wicked
        # Without a chart id the whole library is searched, as before.
        assert third.radarr_movie is old
        assert (
            matcher.match_movie(_chart("Wicked", None), [new, old, wicked]).radarr_movie
            is wicked
        )

    def test_skipped_film_is_logged(self, caplog):
        library = [_lib("Superman", 1978, "tt0078346")]

        with caplog.at_level("INFO"):
            MovieMatcher().match_batch([_chart("Superman", "tt5950044")], library)

        assert any(
            "tt5950044" in r.message
            and "tt0078346" in r.message
            and "different IMDb ids" in r.message
            for r in caplog.records
        )


# (chart title, chart IMDb id, library): the title match lands on an older
# film with another IMDb id, and the only film without an id is another film
# of the franchise, not one of the chart's whole title.
FRANCHISE_FILM_WITHOUT_AN_ID = [
    pytest.param(
        "Avatar: Fire and Ash",
        "tt1757678",
        [("Avatar", 2009, "tt0499549"), ("Avatar: The Way of Water", 2022, None)],
        id="avatar-fire-and-ash-vs-way-of-water",
    ),
    pytest.param(
        "Avatar: Fire and Ash",
        "tt1757678",
        [("Avatar", 2009, "tt0499549"), ("Avatar 4", 2029, None)],
        id="avatar-fire-and-ash-vs-avatar-4",
    ),
    pytest.param(
        "Mission: Impossible - The Final Reckoning",
        "tt9603208",
        [
            ("Mission: Impossible", 1996, "tt0117060"),
            ("Mission: Impossible - Dead Reckoning Part One", 2023, None),
        ],
        id="final-reckoning-vs-dead-reckoning",
    ),
]


class TestNoLossyMatchPastTheSkippedFilm:
    """Skipping the film with another IMDb id does not run the lossy title
    fallbacks over the rest of the library: only a film of the chart's whole
    title can be taken instead."""

    @ENTRY_POINTS
    @pytest.mark.parametrize("title, imdb_id, library", FRANCHISE_FILM_WITHOUT_AN_ID)
    @pytest.mark.parametrize("reverse", [False, True], ids=["id-first", "id-last"])
    def test_another_film_of_the_franchise_is_not_matched(
        self, match, title, imdb_id, library, reverse
    ):
        films = [_lib(*film) for film in library]
        if reverse:
            films.reverse()

        result = match(MovieMatcher(), _chart(title, imdb_id), films)

        assert result == MatchResult(box_office_movie=result.box_office_movie)

    @ENTRY_POINTS
    def test_subtitled_title_still_matches_a_film_without_an_id(self, match):
        """Nothing in the library conflicts with the title match: the lossy
        fallbacks apply as before, other films with ids notwithstanding."""
        f1 = _lib("F1", 2025, None)
        library = [_lib("Heat", 1995, "tt0113277"), f1]

        result = match(MovieMatcher(), _chart("F1: The Movie", "tt16311594"), library)

        assert result.radarr_movie is f1
