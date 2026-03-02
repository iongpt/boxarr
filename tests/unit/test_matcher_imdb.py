"""Unit tests for IMDb-based matching in MovieMatcher."""

import pytest

from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MovieMatcher
from src.core.radarr import RadarrMovie


def _radarr_movie(title, imdb_id=None, tmdb_id=1):
    return RadarrMovie(id=1, title=title, tmdbId=tmdb_id, imdbId=imdb_id)


class TestImdbMatching:
    """Test IMDb-based matching in MovieMatcher."""

    def setup_method(self):
        self.matcher = MovieMatcher()

    def test_imdb_match_with_non_english_title(self):
        """IMDb match works even when titles differ (e.g., French vs English)."""
        radarr_movies = [
            _radarr_movie("La Femme de M\u00e9nage", imdb_id="tt27047903"),
        ]
        self.matcher.build_movie_index(radarr_movies)

        box_movie = BoxOfficeMovie(
            rank=1,
            title="The Housemaid",
            imdb_id="tt27047903",
            release_url="/release/rl1359839233/",
        )

        result = self.matcher.match_movie(box_movie, radarr_movies)

        assert result.is_matched
        assert result.confidence == 1.0
        assert result.match_method == "imdb_id"
        assert result.radarr_movie.title == "La Femme de M\u00e9nage"

    def test_fallback_to_title_when_no_imdb_id(self):
        """Falls back to title matching when imdb_id is None."""
        radarr_movies = [
            _radarr_movie("Wicked", imdb_id="tt1262426"),
        ]
        self.matcher.build_movie_index(radarr_movies)

        box_movie = BoxOfficeMovie(rank=1, title="Wicked")  # No imdb_id

        result = self.matcher.match_movie(box_movie, radarr_movies)

        assert result.is_matched
        assert result.match_method != "imdb_id"

    def test_fallback_when_imdb_not_in_library(self):
        """Falls back to title matching when IMDb ID not in Radarr library."""
        radarr_movies = [
            _radarr_movie("Wicked", imdb_id="tt1262426"),
        ]
        self.matcher.build_movie_index(radarr_movies)

        box_movie = BoxOfficeMovie(
            rank=1,
            title="Wicked",
            imdb_id="tt9999999",  # Not in library
        )

        result = self.matcher.match_movie(box_movie, radarr_movies)

        # Should still match by title
        assert result.is_matched
        assert result.match_method != "imdb_id"

    def test_no_match_when_neither_imdb_nor_title(self):
        """No match when IMDb ID missing and title doesn't match."""
        radarr_movies = [
            _radarr_movie("Totally Different Movie", imdb_id="tt0000001"),
        ]
        self.matcher.build_movie_index(radarr_movies)

        box_movie = BoxOfficeMovie(
            rank=1,
            title="The Housemaid",
            imdb_id="tt9999999",
        )

        result = self.matcher.match_movie(box_movie, radarr_movies)

        assert not result.is_matched

    def test_imdb_match_in_batch(self):
        """IMDb matching works in match_batch."""
        radarr_movies = [
            _radarr_movie("La Femme de M\u00e9nage", imdb_id="tt27047903"),
            _radarr_movie("Wicked", imdb_id="tt1262426"),
        ]

        box_movies = [
            BoxOfficeMovie(rank=1, title="The Housemaid", imdb_id="tt27047903"),
            BoxOfficeMovie(rank=2, title="Wicked", imdb_id="tt1262426"),
            BoxOfficeMovie(rank=3, title="Unknown Movie"),
        ]

        results = self.matcher.match_batch(box_movies, radarr_movies)

        assert len(results) == 3
        assert results[0].is_matched
        assert results[0].match_method == "imdb_id"
        assert results[0].confidence == 1.0
        assert results[1].is_matched
        assert results[1].match_method == "imdb_id"
        assert not results[2].is_matched

    def test_match_movie_single_uses_imdb(self):
        """match_movie (single) also uses IMDb matching."""
        radarr_movies = [
            _radarr_movie("El Ama de Casa", imdb_id="tt27047903"),
        ]
        self.matcher.build_movie_index(radarr_movies)

        box_movie = BoxOfficeMovie(
            rank=1,
            title="The Housemaid",
            imdb_id="tt27047903",
        )

        result = self.matcher.match_movie(box_movie, radarr_movies)

        assert result.is_matched
        assert result.match_method == "imdb_id"
        assert result.confidence == 1.0
