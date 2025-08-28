"""Unit tests for movie matching algorithm."""

import pytest

from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MovieMatcher
from src.core.radarr import RadarrMovie


class TestMovieMatcher:
    """Test movie matching functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.matcher = MovieMatcher(min_confidence=0.75)

        # Sample Radarr movies
        self.radarr_movies = [
            RadarrMovie(
                id=1,
                title="The Dark Knight",
                tmdbId=155,
                year=2008,
                hasFile=True,
            ),
            RadarrMovie(
                id=2,
                title="Spider-Man: No Way Home",
                tmdbId=634649,
                year=2021,
                hasFile=True,
            ),
            RadarrMovie(
                id=3,
                title="Avengers: Endgame",
                tmdbId=299534,
                year=2019,
                hasFile=False,
            ),
            RadarrMovie(
                id=4,
                title="Top Gun: Maverick",
                tmdbId=361743,
                year=2022,
                hasFile=True,
            ),
            RadarrMovie(
                id=5,
                title="Frozen II",
                tmdbId=330457,
                year=2019,
                hasFile=True,
            ),
        ]

    def test_exact_title_match(self):
        """Test matching with exact title."""
        self.matcher.build_movie_index(self.radarr_movies)

        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="The Dark Knight",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, self.radarr_movies)

        assert result.is_matched
        assert result.radarr_movie.id == 1
        assert result.confidence >= 0.95
        assert result.match_method == "exact"

    def test_normalized_title_match(self):
        """Test matching with normalized titles (case, punctuation)."""
        self.matcher.build_movie_index(self.radarr_movies)

        # Different case and punctuation
        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="the dark knight",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, self.radarr_movies)

        assert result.is_matched
        assert result.radarr_movie.id == 1
        assert result.confidence >= 0.9

    def test_subtitle_handling(self):
        """Test matching movies with subtitles (colon vs no colon)."""
        self.matcher.build_movie_index(self.radarr_movies)

        # Box office often drops the colon
        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="Spider-Man No Way Home",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, self.radarr_movies)

        assert result.is_matched
        assert result.radarr_movie.id == 2
        assert result.confidence >= 0.85

    def test_sequel_with_roman_numerals(self):
        """Test matching sequels with Roman numerals."""
        self.matcher.build_movie_index(self.radarr_movies)

        # Box office might use "Frozen 2" instead of "Frozen II"
        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="Frozen 2",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, self.radarr_movies)

        assert result.is_matched
        assert result.radarr_movie.id == 5
        assert result.confidence >= 0.8

    def test_no_match_below_threshold(self):
        """Test that poor matches are not returned."""
        self.matcher.build_movie_index(self.radarr_movies)

        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="Completely Different Movie",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, self.radarr_movies)

        assert not result.is_matched
        assert result.confidence < self.matcher.min_confidence

    def test_year_disambiguation(self):
        """Test that year helps disambiguate similar titles."""
        # Add a remake to the list
        movies_with_remake = self.radarr_movies + [
            RadarrMovie(
                id=6,
                title="Top Gun",
                tmdbId=744,
                year=1986,
                hasFile=True,
            )
        ]

        self.matcher.build_movie_index(movies_with_remake)

        # Should match the newer one based on recency
        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="Top Gun Maverick",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, movies_with_remake)

        assert result.is_matched
        assert result.radarr_movie.id == 4  # The 2022 version

    def test_the_prefix_handling(self):
        """Test handling of 'The' prefix in titles."""
        self.matcher.build_movie_index(self.radarr_movies)

        # Box office might drop "The"
        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="Dark Knight",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, self.radarr_movies)

        assert result.is_matched
        assert result.radarr_movie.id == 1
        assert result.confidence >= 0.8

    def test_batch_matching(self):
        """Test matching multiple movies at once."""
        self.matcher.build_movie_index(self.radarr_movies)

        box_office_movies = [
            BoxOfficeMovie(rank=1, title="Top Gun: Maverick"),
            BoxOfficeMovie(rank=2, title="The Dark Knight"),
            BoxOfficeMovie(rank=3, title="Unknown Movie"),
        ]

        results = self.matcher.match_batch(box_office_movies, self.radarr_movies)

        assert len(results) == 3
        assert results[0].is_matched
        assert results[0].radarr_movie.id == 4
        assert results[1].is_matched
        assert results[1].radarr_movie.id == 1
        assert not results[2].is_matched

    def test_empty_movie_list(self):
        """Test behavior with empty Radarr movie list."""
        self.matcher.build_movie_index([])

        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="Any Movie",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, [])

        assert not result.is_matched
        assert result.confidence == 0.0

    def test_special_characters_in_title(self):
        """Test matching titles with special characters."""
        movies = [
            RadarrMovie(
                id=7,
                title="Fast & Furious 9",
                tmdbId=385128,
                year=2021,
                hasFile=True,
            )
        ]

        self.matcher.build_movie_index(movies)

        # Box office might simplify special characters
        box_office_movie = BoxOfficeMovie(
            rank=1,
            title="Fast and Furious 9",
            weekend_gross=1000000,
        )

        result = self.matcher.match_single(box_office_movie.title, movies)

        assert result.is_matched
        assert result.confidence >= 0.8
