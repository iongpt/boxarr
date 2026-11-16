"""#135 follow-up: a chart entry with an IMDb id is matched by it or by its
whole title, never by the lossy title fallbacks.

The IMDb veto (#161, #168) only rejects a library film whose IMDb id differs.
When the original film is not in the library, the lossy fallbacks (base title
cut at ':' or ' - ', the colon and numeral special cases, fuzzy) could still
link the chart entry to a franchise film that Radarr lists without an IMDb
id: "Avatar: Fire and Ash" to "Avatar 4", "Wicked: For Good" to "Wicked".

A library film with the chart's id is found by the id. So for a chart entry
with an id, the title match may only add a film without an id, and only one
of the chart's whole title: the same words, give or take the leading article,
case, punctuation, accents, "&" for "and" and a hyphen for a space. Entries
without an id keep every fallback.

Titles, years and IMDb ids below are the real ones.
"""

import logging

import pytest

from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MatchResult, MovieMatcher
from src.core.radarr import RadarrMovie

_next_id = iter(range(1, 10_000))


def _lib(title, year, imdb_id=None):
    rid = next(_next_id)
    return RadarrMovie(
        id=rid, title=title, year=year, tmdbId=200_000 + rid, imdbId=imdb_id
    )


def _chart(title, imdb_id):
    return BoxOfficeMovie(rank=1, title=title, imdb_id=imdb_id)


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


# (chart title, chart IMDb id, library film without an IMDb id): the lossy
# fallbacks used to link these; the library film is another film.
ANOTHER_FILM_WITHOUT_AN_ID = [
    pytest.param(
        "Avatar: Fire and Ash",
        "tt1757678",
        ("Avatar", 2009),
        id="fire-and-ash-vs-avatar",
    ),
    pytest.param(
        "Avatar: Fire and Ash",
        "tt1757678",
        ("Avatar 4", 2029),
        id="fire-and-ash-vs-avatar-4",
    ),
    pytest.param(
        "Avatar: Fire and Ash",
        "tt1757678",
        ("Avatar: The Way of Water", 2022),
        id="fire-and-ash-vs-way-of-water",
    ),
    # The 2009 film's re-release: the index's "exact" stage holds base
    # titles too, so this was an "exact" match at confidence 1.0.
    pytest.param(
        "Avatar",
        "tt0499549",
        ("Avatar: The Way of Water", 2022),
        id="avatar-vs-way-of-water",
    ),
    pytest.param(
        "Wicked: For Good", "tt19847976", ("Wicked", 2024), id="for-good-vs-wicked"
    ),
    pytest.param(
        "Spider-Man: Brand New Day",
        "tt22084616",
        ("Spider-Man: No Way Home", 2021),
        id="brand-new-day-vs-no-way-home",
    ),
    pytest.param(
        "Spider-Man: Brand New Day",
        "tt22084616",
        ("Spider-Man", 2002),
        id="brand-new-day-vs-spider-man",
    ),
    pytest.param(
        "Mission: Impossible - The Final Reckoning",
        "tt9603208",
        ("Mission: Impossible", 1996),
        id="final-reckoning-vs-mission-impossible",
    ),
    pytest.param(
        "Superman", "tt5950044", ("Superman II", 1980), id="superman-vs-superman-ii"
    ),
]


class TestNoLossyMatchForAnEntryWithAnId:
    @ENTRY_POINTS
    @pytest.mark.parametrize("title, imdb_id, film", ANOTHER_FILM_WITHOUT_AN_ID)
    def test_film_without_an_id_is_not_matched_by_a_lossy_fallback(
        self, match, title, imdb_id, film
    ):
        box_movie = _chart(title, imdb_id)

        result = match(MovieMatcher(), box_movie, [_lib(*film)])

        assert result == MatchResult(box_office_movie=box_movie)

    @ENTRY_POINTS
    def test_same_film_under_a_shorter_title_is_not_matched_by_title(self, match):
        """Box Office Mojo's "F1: The Movie" is Radarr's "F1". Without the
        IMDb id on the library film nothing in the titles tells it from a
        franchise film found the same way, so the matcher leaves the entry
        unmatched. The status refresh can still link it by the TMDB film its
        card was looked up as, and the id path links it once Radarr has the
        id (see the controls below)."""
        box_movie = _chart("F1: The Movie", "tt16311594")

        result = match(MovieMatcher(), box_movie, [_lib("F1", 2025)])

        assert result == MatchResult(box_office_movie=box_movie)

    @ENTRY_POINTS
    def test_numeral_spelling_is_not_a_whole_title(self, match):
        """The ladder finds "Gladiator 2" through the base title "Gladiator";
        an entry with an id needs the film's id or its whole title."""
        box_movie = _chart("Gladiator II", "tt9218128")

        result = match(MovieMatcher(), box_movie, [_lib("Gladiator 2", 2024)])

        assert result == MatchResult(box_office_movie=box_movie)

    @ENTRY_POINTS
    def test_whole_title_film_is_taken_over_a_lossy_one(self, match):
        library = [_lib("Avatar 4", 2029), _lib("Avatar: Fire and Ash", 2025)]

        result = match(
            MovieMatcher(), _chart("Avatar: Fire and Ash", "tt1757678"), library
        )

        assert result.radarr_movie is library[1]
        assert result.match_method == "exact"

    @ENTRY_POINTS
    @pytest.mark.parametrize("smurfs_first", [False, True], ids=["last", "first"])
    def test_lossy_pick_does_not_hide_a_whole_title_film(self, match, smurfs_first):
        """The exact stage finds "The Smurfs 2" by its base title "The
        Smurfs". That pick is rejected, and the whole-title replacement then
        takes "Smurfs", the entry's title without its article - the film the
        entry gets when "The Smurfs 2" is not in the library at all. "Smurfs"
        (2025) is not the charted 2011 film, but nothing in the titles tells
        them apart; main linked "The Smurfs 2", also another film."""
        sequel, smurfs = _lib("The Smurfs 2", 2013), _lib("Smurfs", 2025)
        library = [smurfs, sequel] if smurfs_first else [sequel, smurfs]
        box_movie = _chart("The Smurfs", "tt0472181")

        result = match(MovieMatcher(), box_movie, library)
        alone = match(MovieMatcher(), box_movie, [smurfs])

        assert result.radarr_movie is smurfs
        assert (result.match_method, result.confidence) == ("normalized", 0.95)
        assert (alone.radarr_movie, alone.match_method, alone.confidence) == (
            smurfs,
            "normalized",
            0.95,
        )

    def test_rejected_film_is_logged(self, caplog):
        library = [_lib("Wicked", 2024)]

        with caplog.at_level(logging.INFO):
            MovieMatcher().match_batch(
                [_chart("Wicked: For Good", "tt19847976")], library
            )

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "'Wicked: For Good' (tt19847976)" in m
            and "'Wicked'" in m
            and "no IMDb id" in m
            for m in messages
        )
        assert not any("different IMDb ids" in m for m in messages)


class TestWholeTitleAndIdMatchesAreKept:
    """Controls: these matched before and still do."""

    @ENTRY_POINTS
    @pytest.mark.parametrize(
        "title, imdb_id, film, method",
        [
            pytest.param(
                "Superman", "tt5950044", ("Superman", 2025), "exact", id="exact"
            ),
            pytest.param(
                "Smurfs", "tt23251300", ("Smurfs", 2025), "exact", id="exact-smurfs"
            ),
            pytest.param(
                "The Running Man",
                "tt14107334",
                ("Running Man", 2025),
                "normalized",
                id="without-article",
            ),
        ],
    )
    def test_whole_title_matches_a_film_without_an_id(
        self, match, title, imdb_id, film, method
    ):
        library = [_lib(*film)]

        result = match(MovieMatcher(), _chart(title, imdb_id), library)

        assert result.radarr_movie is library[0]
        assert result.match_method == method

    @ENTRY_POINTS
    @pytest.mark.parametrize(
        "title, imdb_id, film",
        [
            pytest.param(
                "Harry Potter and the Half-Blood Prince",
                "tt0417741",
                ("Harry Potter and the Half Blood Prince", 2009),
                id="hyphen-vs-space",
            ),
            pytest.param(
                "E.T. the Extra-Terrestrial",
                "tt0083866",
                ("E.T. the Extra Terrestrial", 1982),
                id="hyphen-vs-space-et",
            ),
            pytest.param(
                "Kalki 2898-AD",
                "tt12735488",
                ("Kalki 2898 AD", 2024),
                id="hyphen-vs-space-kalki",
            ),
            pytest.param(
                "Pokémon Detective Pikachu",
                "tt5884052",
                ("Pokemon Detective Pikachu", 2019),
                id="accent-dropped",
            ),
            pytest.param(
                "Dungeons & Dragons: Honor Among Thieves",
                "tt2906216",
                ("Dungeons and Dragons: Honor Among Thieves", 2023),
                id="ampersand-vs-and",
            ),
        ],
    )
    def test_same_words_spelled_differently_match_a_film_without_an_id(
        self, match, title, imdb_id, film
    ):
        """The fuzzy step finds the film, as on main, and it is the entry's
        whole title: the same words, spelled with a hyphen for a space, an
        accent or "and" for "&". normalize_title alone glues "Half-Blood"
        into "halfblood", which "Half Blood" never equals."""
        library = [_lib(*film)]

        result = match(MovieMatcher(), _chart(title, imdb_id), library)

        assert result.radarr_movie is library[0]
        assert result.match_method == "fuzzy"

    @ENTRY_POINTS
    def test_whole_title_pick_keeps_its_method_and_confidence(self, match):
        """The colon special case finds "Mission Impossible", the entry's
        whole title: the ladder's pick stands as on main, method "special" at
        0.85, rather than being re-found by the whole-title replacement."""
        library = [_lib("Mission Impossible", 1996)]

        result = match(
            MovieMatcher(), _chart("Mission: Impossible", "tt0117060"), library
        )

        assert result.radarr_movie is library[0]
        assert (result.match_method, result.confidence) == ("special", 0.85)

    @ENTRY_POINTS
    def test_whole_title_pick_among_same_title_films_is_mains(self, match):
        """Two films of the entry's title, neither with an id: the ladder's
        pick stands, as on main (the index keeps the last one listed). The
        whole-title replacement would take the first."""
        library = [_lib("Lilo & Stitch", 2002), _lib("Lilo & Stitch", 2025)]

        result = match(MovieMatcher(), _chart("Lilo & Stitch", "tt11655566"), library)

        assert result.radarr_movie is library[-1]
        assert (result.match_method, result.confidence) == ("exact", 1.0)

    @ENTRY_POINTS
    def test_replacement_takes_the_same_words_after_a_veto(self, match):
        """The #168 replacement compares titles the same way: the 2002 film
        has another id and is skipped, and the new film Radarr lists without
        an id, spelled with "and", is taken."""
        library = [
            _lib("Lilo & Stitch", 2002, "tt0275847"),
            _lib("Lilo and Stitch", 2025),
        ]

        result = match(MovieMatcher(), _chart("Lilo & Stitch", "tt11655566"), library)

        assert result.radarr_movie is library[1]
        assert result.match_method == "normalized"

    @ENTRY_POINTS
    def test_film_with_the_id_matches_whatever_its_title(self, match):
        library = [_lib("F1", 2025, "tt16311594")]

        result = match(MovieMatcher(), _chart("F1: The Movie", "tt16311594"), library)

        assert result.radarr_movie is library[0]
        assert result.match_method == "imdb_id"

    @ENTRY_POINTS
    def test_film_with_the_id_spelled_differently_matches_by_title(self, match):
        """The id misses the index, which is keyed on the exact spelling; the
        title match lands on the film, and its id is the chart's."""
        library = [_lib("F1", 2025, " TT16311594")]

        result = match(MovieMatcher(), _chart("F1: The Movie", "tt16311594"), library)

        assert result.radarr_movie is library[0]
        assert result.match_method == "normalized"

    @ENTRY_POINTS
    @pytest.mark.parametrize(
        "title, film",
        [
            pytest.param("F1: The Movie", ("F1", 2025), id="f1-the-movie-vs-f1"),
            pytest.param(
                "Avatar: Fire and Ash", ("Avatar", 2009), id="fire-and-ash-vs-avatar"
            ),
        ],
    )
    def test_entry_without_an_id_keeps_the_lossy_fallbacks(self, match, title, film):
        """Weeks stored before v2.2.0 and Mojo rows without a release link
        have no id: title matching is all there is, and is unchanged."""
        library = [_lib(*film)]

        result = match(MovieMatcher(), _chart(title, None), library)

        assert result.radarr_movie is library[0]
        assert result.match_method == "normalized"
