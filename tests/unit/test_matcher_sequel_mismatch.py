"""Regression tests for the sequel-vs-original matching defect.

When a charting sequel is absent from the Radarr library but the original is
present, the digit/numeral-stripped base-title fallback used to link the sequel
to the original at high confidence, so auto-add skipped it and the weekly page
showed the wrong film. These tests pin the fix and guard every intended
equivalence that must survive it.
"""

from src.core.matcher import MovieMatcher
from src.core.models import MovieStatus
from src.core.radarr import RadarrMovie


def _movie(id: int, title: str, year: int | None = None) -> RadarrMovie:
    return RadarrMovie(
        id=id,
        title=title,
        tmdbId=id * 1000,
        year=year,
        status=MovieStatus.RELEASED,
        hasFile=False,
    )


class TestSequelDoesNotMatchOriginal:
    """The reported defect: sequel query must not fall back to the original."""

    def test_gladiator_two_does_not_match_gladiator_original(self):
        """'Gladiator II' must NOT match a library holding only 'Gladiator' (2000)."""
        library = [_movie(1, "Gladiator", 2000)]
        matcher = MovieMatcher()  # production default (0.95)
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator II", library)

        assert not result.is_matched
        assert result.radarr_movie is None

    def test_gladiator_two_arabic_form_does_not_match_original(self):
        """The arabic form 'Gladiator 2' is guarded the same as the Roman form."""
        library = [_movie(1, "Gladiator", 2000)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator 2", library)

        assert not result.is_matched

    def test_sonic_three_does_not_match_sonic_original(self):
        """'Sonic the Hedgehog 3' must NOT match 'Sonic the Hedgehog'."""
        library = [_movie(1, "Sonic the Hedgehog", 2020)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Sonic the Hedgehog 3", library)

        assert not result.is_matched

    def test_part_marker_sequel_does_not_match_original(self):
        """A 'Part N' marker is treated as an explicit sequel marker."""
        library = [_movie(1, "Dune", 2021)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Dune Part Two", library)

        assert not result.is_matched

    def test_different_part_numbers_do_not_match(self):
        """'Part 2' must not collapse onto a library 'Part 1'."""
        library = [_movie(1, "Harry Potter and the Deathly Hallows: Part 1", 2010)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single(
            "Harry Potter and the Deathly Hallows: Part 2", library
        )

        assert not result.is_matched

    def test_low_threshold_still_refuses_sequel_to_original(self):
        """Even a permissive fuzzy threshold must not resurrect the original."""
        library = [_movie(1, "Gladiator", 2000)]
        matcher = MovieMatcher(min_confidence=0.8)
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator II", library)

        assert not result.is_matched


class TestMarkerEquivalenceStillMatches:
    """Roman<->arabic equivalence between sequels must keep working."""

    def test_gladiator_roman_matches_arabic_sequel(self):
        """'Gladiator II' matches library 'Gladiator 2' (same film, different form)."""
        library = [_movie(1, "Gladiator", 2000), _movie(2, "Gladiator 2", 2024)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator II", library)

        assert result.is_matched
        assert result.radarr_movie.title == "Gladiator 2"

    def test_gladiator_arabic_matches_arabic_sequel(self):
        """'Gladiator 2' matches library 'Gladiator 2' when both originals exist."""
        library = [_movie(1, "Gladiator", 2000), _movie(2, "Gladiator 2", 2024)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator 2", library)

        assert result.is_matched
        assert result.radarr_movie.title == "Gladiator 2"

    def test_frozen_arabic_matches_roman_sequel(self):
        """'Frozen 2' matches library 'Frozen II' (the documented equivalence)."""
        library = [_movie(1, "Frozen II", 2019)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Frozen 2", library)

        assert result.is_matched
        assert result.radarr_movie.title == "Frozen II"

    def test_frozen_roman_matches_arabic_sequel(self):
        """'Frozen II' matches library 'Frozen 2' (reverse form equivalence)."""
        library = [_movie(1, "Frozen 2", 2019)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Frozen II", library)

        assert result.is_matched
        assert result.radarr_movie.title == "Frozen 2"

    def test_part_two_matches_part_ii_equivalent(self):
        """'... Part Two' matches a library '... Part 2' via equivalent markers."""
        library = [_movie(1, "The Hunger Games: Mockingjay - Part 2", 2015)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single(
            "The Hunger Games: Mockingjay - Part Two", library
        )

        assert result.is_matched
        assert result.radarr_movie.title == "The Hunger Games: Mockingjay - Part 2"

    def test_correct_sequel_preferred_when_both_present(self):
        """With original and sequel present, the sequel query picks the sequel."""
        library = [
            _movie(1, "Sonic the Hedgehog", 2020),
            _movie(2, "Sonic the Hedgehog 3", 2024),
        ]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Sonic the Hedgehog 3", library)

        assert result.is_matched
        assert result.radarr_movie.title == "Sonic the Hedgehog 3"


class TestYearCorroboration:
    """A release-year match within one year overrides the marker guard."""

    def test_same_year_corroborates_same_film(self):
        """Matching years mean the marker mismatch is treated as the same film."""
        library = [_movie(1, "Gladiator", 2000)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        # A charting entry re-released the same year as the library entry.
        result = matcher.match_single("Gladiator II (2000)", library)

        assert result.is_matched
        assert result.radarr_movie.title == "Gladiator"

    def test_year_within_one_corroborates(self):
        """A one-year gap still corroborates (boundary, inclusive)."""
        library = [_movie(1, "Gladiator", 2000)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator II (2001)", library)

        assert result.is_matched
        assert result.radarr_movie.title == "Gladiator"

    def test_year_gap_of_two_does_not_corroborate(self):
        """A two-year gap is outside the window and the guard holds."""
        library = [_movie(1, "Gladiator", 2000)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator II (2002)", library)

        assert not result.is_matched

    def test_real_sequel_year_does_not_corroborate(self):
        """The real 'Gladiator II' (2024) never corroborates 'Gladiator' (2000)."""
        library = [_movie(1, "Gladiator", 2000)]
        matcher = MovieMatcher()
        matcher.build_movie_index(library)

        result = matcher.match_single("Gladiator II (2024)", library)

        assert not result.is_matched


class TestUnmarkedBehaviorPreserved:
    """Behaviors without an explicit query marker must be untouched."""

    def setup_method(self):
        self.matcher = MovieMatcher(min_confidence=0.8)
        self.library = [
            _movie(1, "Spider-Man: No Way Home", 2021),
            _movie(2, "The Batman", 2022),
            _movie(3, "Batman", 1989),
            _movie(4, "The Dark Knight", 2008),
            _movie(5, "The Fantastic 4: First Steps", 2025),
            _movie(6, "Ocean's 11", 2001),
            _movie(7, "Wicked", 2024),
        ]
        self.matcher.build_movie_index(self.library)

    def test_exact_match_preserved(self):
        result = self.matcher.match_single("Wicked", self.library)
        assert result.is_matched
        assert result.match_method == "exact"

    def test_the_batman_prefers_the_batman(self):
        """'The Batman' handling stays as documented."""
        result = self.matcher.match_single("The Batman", self.library)
        assert result.is_matched
        assert result.radarr_movie.title == "The Batman"

    def test_batman_without_article_matches_older_batman(self):
        result = self.matcher.match_single("Batman", self.library)
        assert result.is_matched
        assert result.radarr_movie.title == "Batman"
        assert result.radarr_movie.year == 1989

    def test_dark_knight_matches_the_dark_knight(self):
        result = self.matcher.match_single("Dark Knight", self.library)
        assert result.is_matched
        assert result.radarr_movie.title == "The Dark Knight"

    def test_brand_number_in_library_still_matches_word_query(self):
        """A plain word query still reaches a numbered brand title ('Fantastic 4')."""
        result = self.matcher.match_single(
            "The Fantastic Four: First Steps", self.library
        )
        assert result.is_matched
        assert result.radarr_movie.title == "The Fantastic 4: First Steps"

    def test_word_form_query_matches_numbered_brand(self):
        """'Ocean's Eleven' still matches library 'Ocean's 11' (number is brand)."""
        result = self.matcher.match_single("Ocean's Eleven", self.library)
        assert result.is_matched
        assert result.radarr_movie.title == "Ocean's 11"

    def test_fuzzy_typo_still_matches(self):
        result = self.matcher.match_single("Spiderman: No Way Home", self.library)
        assert result.is_matched
        assert result.radarr_movie.title == "Spider-Man: No Way Home"

    def test_unrelated_title_still_no_match(self):
        result = self.matcher.match_single("Some Nonexistent Film", self.library)
        assert not result.is_matched
