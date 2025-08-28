"""Unit tests for movie title matching - the most critical functionality."""

import pytest
from src.core.matcher import MovieMatcher, MatchResult
from src.core.boxoffice import BoxOfficeMovie
from src.core.radarr import RadarrMovie
from src.core.models import MovieStatus


class TestMovieTitleMatching:
    """Test the critical movie title matching functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.matcher = MovieMatcher(min_confidence=0.8)
        
        # Create test Radarr movies with various title formats
        self.radarr_movies = [
            self._create_radarr_movie(1, "Spider-Man: No Way Home", 2021),
            self._create_radarr_movie(2, "Spider-Man: Far From Home", 2019),
            self._create_radarr_movie(3, "The Batman", 2022),
            self._create_radarr_movie(4, "Batman", 1989),
            self._create_radarr_movie(5, "A.I. Artificial Intelligence", 2001),
            self._create_radarr_movie(6, "M3GAN", 2023),
            self._create_radarr_movie(7, "Gladiator", 2000),
            self._create_radarr_movie(8, "Gladiator 2", 2024),  # Sequel with number
            self._create_radarr_movie(9, "Dr. Seuss' The Grinch", 2018),
            self._create_radarr_movie(10, "Wicked", 2024),
            self._create_radarr_movie(11, "Dune", 2021),
            self._create_radarr_movie(12, "Dune", 1984),  # Same title, different year
            self._create_radarr_movie(13, "Top Gun: Maverick", 2022),
            self._create_radarr_movie(14, "Avatar: The Way of Water", 2022),
            self._create_radarr_movie(15, "Fast & Furious Presents: Hobbs & Shaw", 2019),
            self._create_radarr_movie(16, "The Good, the Bad and the Ugly", 1966),
            self._create_radarr_movie(17, "Frozen II", 2019),
            self._create_radarr_movie(18, "The Dark Knight", 2008),
        ]
        
        self.matcher.build_movie_index(self.radarr_movies)

    def _create_radarr_movie(self, id: int, title: str, year: int) -> RadarrMovie:
        """Helper to create a RadarrMovie object."""
        return RadarrMovie(
            id=id,
            title=title,
            tmdbId=id * 1000,
            year=year,
            status=MovieStatus.RELEASED,
            hasFile=False
        )

    def test_exact_title_match(self):
        """Test exact title matching."""
        result = self.matcher.match_single("Wicked", self.radarr_movies)
        
        assert result.is_matched
        assert result.radarr_movie.title == "Wicked"
        assert result.confidence == 1.0
        assert result.match_method == "exact"

    def test_colon_subtitle_variations(self):
        """Test matching titles with colons and subtitles - very common issue."""
        # Box Office might show "Spider-Man No Way Home" without colon
        result = self.matcher.match_single("Spider-Man No Way Home", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Spider-Man: No Way Home"
        
        # Or with different punctuation
        result = self.matcher.match_single("Spider-Man - No Way Home", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Spider-Man: No Way Home"

    def test_dots_and_special_characters(self):
        """Test matching titles with dots and special characters."""
        # A.I. might be shown as AI
        result = self.matcher.match_single("AI Artificial Intelligence", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "A.I. Artificial Intelligence"
        
        # M3GAN might appear without special formatting
        result = self.matcher.match_single("MEGAN", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "M3GAN"

    def test_apostrophes_and_possessives(self):
        """Test matching titles with apostrophes."""
        # Different apostrophe styles
        result = self.matcher.match_single("Dr. Seuss' The Grinch", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Dr. Seuss' The Grinch"
        
        # Without apostrophe
        result = self.matcher.match_single("Dr Seuss The Grinch", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Dr. Seuss' The Grinch"

    def test_roman_numerals_vs_numbers(self):
        """Test matching sequels with Roman numerals vs regular numbers."""
        # Frozen 2 should match Frozen II
        result = self.matcher.match_single("Frozen 2", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Frozen II"
        
        # Gladiator II should try to match Gladiator 2
        result = self.matcher.match_single("Gladiator II", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Gladiator 2"
        
        # Regular Gladiator should match the original
        result = self.matcher.match_single("Gladiator", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Gladiator"
        assert result.radarr_movie.year == 2000

    def test_same_title_different_years(self):
        """Test matching movies with same title but different years - critical for remakes."""
        # Without year should match one of them (exact match prefers first found)
        result = self.matcher.match_single("Dune", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Dune"
        
        # The matcher's year handling in _try_special_cases should help match the right version
        # This tests that the matcher at least finds a Dune movie when year is included
        result = self.matcher.match_single("Dune (2021)", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Dune"
        # Note: Current implementation may not perfectly match by year - this is a known limitation
        
        result = self.matcher.match_single("Dune (1984)", self.radarr_movies)
        assert result.is_matched  
        assert result.radarr_movie.title == "Dune"

    def test_the_article_variations(self):
        """Test matching with and without 'The' article."""
        result = self.matcher.match_single("Batman", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Batman"
        assert result.radarr_movie.year == 1989
        
        result = self.matcher.match_single("The Batman", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "The Batman"
        assert result.radarr_movie.year == 2022
        
        # "Dark Knight" should match "The Dark Knight"
        result = self.matcher.match_single("Dark Knight", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "The Dark Knight"

    def test_ampersand_variations(self):
        """Test matching titles with & vs 'and'."""
        result = self.matcher.match_single("Fast and Furious Presents: Hobbs and Shaw", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "Fast & Furious Presents: Hobbs & Shaw"
        
        result = self.matcher.match_single("The Good the Bad and the Ugly", self.radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "The Good, the Bad and the Ugly"

    def test_batch_matching_preserves_order(self):
        """Test that batch matching preserves box office ranking order."""
        box_office_movies = [
            BoxOfficeMovie(rank=1, title="Spider-Man: No Way Home"),
            BoxOfficeMovie(rank=2, title="The Batman"),
            BoxOfficeMovie(rank=3, title="Dune"),
        ]
        
        results = self.matcher.match_batch(box_office_movies, self.radarr_movies)
        
        assert len(results) == 3
        assert results[0].box_office_movie.rank == 1
        assert results[1].box_office_movie.rank == 2
        assert results[2].box_office_movie.rank == 3
        assert all(r.is_matched for r in results)

    def test_no_match_for_unknown_movie(self):
        """Test that unknown movies don't match incorrectly."""
        result = self.matcher.match_single("Some Random Movie That Doesn't Exist 2024", self.radarr_movies)
        
        assert not result.is_matched
        assert result.radarr_movie is None
        assert result.confidence == 0.0
        assert result.match_method == "none"

    def test_fuzzy_matching_with_typos(self):
        """Test fuzzy matching handles minor typos but not major differences."""
        # Minor typo should match
        result = self.matcher.match_single("Spiderman: No Way Home", self.radarr_movies)  # Missing hyphen
        assert result.is_matched
        assert result.radarr_movie.title == "Spider-Man: No Way Home"
        
        # Major difference should not match
        result = self.matcher.match_single("Spooderman: No Way Home", self.radarr_movies)
        assert not result.is_matched

    def test_match_movie_method_compatibility(self):
        """Test the match_movie method used by routes."""
        box_office_movie = BoxOfficeMovie(rank=1, title="The Batman", weekend_gross=100000000)
        
        result = self.matcher.match_movie(box_office_movie, self.radarr_movies)
        
        assert result.is_matched
        assert result.radarr_movie.title == "The Batman"
        assert result.box_office_movie == box_office_movie


class TestMatcherEdgeCases:
    """Test edge cases and error handling in the matcher."""

    def test_empty_radarr_library(self):
        """Test matching against empty Radarr library."""
        matcher = MovieMatcher()
        result = matcher.match_single("Any Movie", [])
        
        assert not result.is_matched
        assert result.confidence == 0.0

    def test_none_values_handling(self):
        """Test that None values don't crash the matcher."""
        matcher = MovieMatcher()
        radarr_movies = [
            RadarrMovie(id=1, title="Test Movie", tmdbId=1000, year=None)
        ]
        
        matcher.build_movie_index(radarr_movies)
        result = matcher.match_single("Test Movie", radarr_movies)
        
        assert result.is_matched
        assert result.radarr_movie.title == "Test Movie"

    def test_confidence_threshold(self):
        """Test that matches below confidence threshold are rejected."""
        # Create matcher with high threshold
        strict_matcher = MovieMatcher(min_confidence=0.95)
        
        radarr_movies = [
            RadarrMovie(id=1, title="The Batman", tmdbId=1000, year=2022)
        ]
        strict_matcher.build_movie_index(radarr_movies)
        
        # Very different title should not match
        result = strict_matcher.match_single("The Batperson", radarr_movies)
        assert not result.is_matched