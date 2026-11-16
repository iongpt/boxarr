"""#135 follow-up: a title is cut at a subtitle separator, never inside a word.

``get_base_title()`` used to cut a title at any '-'. "Spider-Man" became
"Spider", "Ant-Man and the Wasp" became "Ant", and "X-Men" became "" (the "X"
left over was then dropped as a Roman numeral). The base title is a key of the
library index and a fuzzy score, so hyphenated films matched unrelated ones:
Cronenberg's "Spider" was linked to "Spider-Man", "X" (2022) to "X-Men", and
"Ant-Man and the Wasp" to "Ant-Man". Now a hyphen or an en dash separates a
subtitle only with whitespace on both sides ("Spider-Man - No Way Home",
"Avatar – Aufbruch nach Pandora"); two hyphens or an em dash cut, spaced or
not. A hyphen or en dash inside a word ("Spider–Man", "1914–1918") or with
whitespace on one side only ("Pre- and Post-War") is part of the title.

The chart entries below carry no IMDb id (weeks stored before v2.2.0, Mojo
rows without a release link), so title matching is all the matcher has.
"""

import pytest

from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MovieMatcher
from src.core.radarr import RadarrMovie


def _lib(*films):
    return [
        RadarrMovie(id=i, title=title, year=year, tmdbId=1000 + i)
        for i, (title, year) in enumerate(films, start=1)
    ]


def _single(title, library):
    return MovieMatcher().match_single(title, library)


def _batch(title, library):
    chart = BoxOfficeMovie(rank=1, title=title)
    return MovieMatcher().match_batch([chart], library)[0]


def _movie(title, library):
    return MovieMatcher().match_movie(BoxOfficeMovie(rank=1, title=title), library)


ENTRY_POINTS = pytest.mark.parametrize(
    "match",
    [_single, _batch, _movie],
    ids=["match_single", "match_batch", "match_movie"],
)


class TestBaseTitle:
    @pytest.mark.parametrize(
        "title",
        [
            "Spider-Man",
            "X-Men",
            "Ant-Man",
            "Ant-Man and the Wasp",
            "Jean-Philippe",
        ],
    )
    def test_hyphenated_word_is_kept_whole(self, title):
        assert MovieMatcher().get_base_title(title) == title

    @pytest.mark.parametrize(
        "title",
        [
            "Spider–Man",
            "Paris–Manhattan",
            "Rock–Paper–Scissors",
            "The Great War 1914–1918",
            "Blade Runner–2049",
        ],
    )
    def test_en_dash_inside_a_word_is_kept_whole(self, title):
        """An en dash joins words and ranges like a hyphen does."""
        assert MovieMatcher().get_base_title(title) == title

    @pytest.mark.parametrize(
        "title",
        [
            # A suspended hyphen and a minus sign.
            "Pre- and Post-War",
            "Minus -1",
            "Spider-Man- No Way Home",
            "Spider-Man -No Way Home",
            # Real Box Office Mojo titles that put a subtitle after a one-sided
            # hyphen: it cannot be told from the two above, so it stays.
            "Whetu Marama- Bright Star",
            "The Irregular at Magic High School THE MOVIE -Yotsuba Succession Arc-",
            # The same for an en dash.
            "Avatar– Aufbruch nach Pandora",
            "Avatar –Aufbruch nach Pandora",
        ],
    )
    def test_one_sided_hyphen_or_en_dash_is_not_a_separator(self, title):
        assert MovieMatcher().get_base_title(title) == title

    @pytest.mark.parametrize(
        "title, base",
        [
            ("Elvis (1935–1977)", "Elvis"),
            ("Oppenheimer [IMAX–70mm]", "Oppenheimer"),
        ],
    )
    def test_en_dash_inside_brackets_leaves_the_brackets_whole(self, title, base):
        """The bracket patterns still remove a range in brackets: the dash
        pattern runs first and must not cut it, leaving "Elvis (1935"."""
        assert MovieMatcher().get_base_title(title) == base

    @pytest.mark.parametrize(
        "title, base",
        [
            ("Spider-Man: Brand New Day", "Spider-Man"),
            ("Spider-Man: Across the Spider-Verse", "Spider-Man"),
            ("Jean-Michel Basquiat: The Radiant Child", "Jean-Michel Basquiat"),
        ],
    )
    def test_colon_cut_keeps_the_hyphenated_word(self, title, base):
        assert MovieMatcher().get_base_title(title) == base

    @pytest.mark.parametrize(
        "title, base",
        [
            # The colon still comes first.
            ("Mission: Impossible - Dead Reckoning Part One", "Mission"),
            # The colon special case looks this form up: a spaced hyphen.
            ("Mission Impossible - The Final Reckoning", "Mission Impossible"),
            ("Spider-Man - No Way Home", "Spider-Man"),
            # Avatar's German title, with an en dash.
            ("Avatar – Aufbruch nach Pandora", "Avatar"),
            ("Mission Impossible – The Final Reckoning", "Mission Impossible"),
            # An em dash, spaced or not.
            ("Mission Impossible—The Final Reckoning", "Mission Impossible"),
            ("Mission Impossible — The Final Reckoning", "Mission Impossible"),
            # Two hyphens stand for a dash.
            ("Mission Impossible--The Final Reckoning", "Mission Impossible"),
            ("Mission Impossible -- The Final Reckoning", "Mission Impossible"),
        ],
    )
    def test_subtitle_separator_cuts(self, title, base):
        assert MovieMatcher().get_base_title(title) == base


# (chart title, library film): unrelated films whose titles shared a base
# title only because a hyphenated word was cut.
UNRELATED_BY_HYPHEN_CUT = [
    pytest.param("Spider", ("Spider-Man", 2002), id="spider-vs-spider-man"),
    pytest.param("Spider-Man", ("Spider", 2002), id="spider-man-vs-spider"),
    pytest.param(
        "Spider-Man: Across the Spider-Verse",
        ("Spider", 2002),
        id="across-the-spider-verse-vs-spider",
    ),
    pytest.param("X", ("X-Men", 2000), id="x-vs-x-men"),
    pytest.param("X-Men", ("X", 2022), id="x-men-vs-x"),
    pytest.param(
        "Ant-Man and the Wasp", ("Ant-Man", 2015), id="ant-man-and-the-wasp-vs-ant-man"
    ),
    pytest.param(
        "Ant-Man", ("Ant-Man and the Wasp", 2018), id="ant-man-vs-ant-man-and-the-wasp"
    ),
    pytest.param(
        "Jean-Philippe",
        ("Jean-Michel Basquiat: The Radiant Child", 2010),
        id="jean-philippe-vs-jean-michel-basquiat",
    ),
    pytest.param(
        "The Great War 1914-1918",
        ("The Great War", 2019),
        id="year-range-vs-title-before-it",
    ),
]

# The same with an en dash. Main never cut at an en dash, so these never
# matched; a dash pattern that cut at any en dash made them match.
UNRELATED_BY_EN_DASH_CUT = [
    pytest.param("Spider–Man", ("Spider", 2002), id="en-dash-spider-man-vs-spider"),
    pytest.param("Spider", ("Spider–Man", 2002), id="spider-vs-en-dash-spider-man"),
    pytest.param(
        "The Great War 1914–1918",
        ("The Great War", 2019),
        id="en-dash-year-range-vs-title-before-it",
    ),
    pytest.param(
        "The Great War",
        ("The Great War 1914–1918", 1964),
        id="title-vs-en-dash-year-range",
    ),
    pytest.param(
        "Blade Runner–2049",
        ("Blade Runner", 1982),
        id="en-dash-blade-runner-2049-vs-blade-runner",
    ),
    pytest.param("Paris", ("Paris–Manhattan", 2012), id="paris-vs-paris-manhattan"),
    pytest.param("Paris–Manhattan", ("Paris", 2008), id="paris-manhattan-vs-paris"),
    pytest.param(
        "Rock", ("Rock–Paper–Scissors", 2012), id="rock-vs-rock-paper-scissors"
    ),
]


class TestHyphenatedTitlesDoNotCollide:
    @ENTRY_POINTS
    @pytest.mark.parametrize(
        "chart_title, film", UNRELATED_BY_HYPHEN_CUT + UNRELATED_BY_EN_DASH_CUT
    )
    def test_unrelated_film_is_not_matched(self, match, chart_title, film):
        result = match(chart_title, _lib(film))

        assert not result.is_matched
        assert result.match_method == "none"

    @ENTRY_POINTS
    @pytest.mark.parametrize("reverse", [False, True], ids=["own-first", "own-last"])
    def test_own_title_beats_another_films_base_title(self, match, reverse):
        """Control: "Spider-Man: No Way Home" now has the base title
        "Spider-Man"; the 2002 film of that very title still wins."""
        library = _lib(("Spider-Man", 2002), ("Spider-Man: No Way Home", 2021))
        if reverse:
            library.reverse()

        result = match("Spider-Man", library)

        assert result.radarr_movie.year == 2002
        assert result.match_method == "exact"

    @ENTRY_POINTS
    def test_spaced_hyphen_variant_still_matches(self, match):
        """Control: the subtitle written after a spaced hyphen."""
        library = _lib(("Spider-Man: No Way Home", 2021), ("Spider-Man", 2002))

        result = match("Spider-Man - No Way Home", library)

        assert result.radarr_movie.title == "Spider-Man: No Way Home"


class TestDashSeparatesASubtitle:
    @ENTRY_POINTS
    def test_en_dash_subtitle_is_cut_on_the_library_side(self, match):
        """A library titled in German ("Avatar – Aufbruch nach Pandora") is
        found by the film's English title, as with a spaced hyphen."""
        library = _lib(("Avatar – Aufbruch nach Pandora", 2009))

        result = match("Avatar", library)

        assert result.radarr_movie is library[0]

    @ENTRY_POINTS
    def test_spaced_hyphen_subtitle_is_cut_on_the_library_side(self, match):
        """Control: the same title with an ASCII hyphen matched before."""
        library = _lib(("Avatar - Aufbruch nach Pandora", 2009))

        result = match("Avatar", library)

        assert result.radarr_movie is library[0]
