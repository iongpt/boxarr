"""Unit tests for movie matching algorithms."""

import pytest
from typing import List

from src.core.matcher import MovieMatcher, MatchResult
from src.core.radarr import RadarrMovie
from src.core.boxoffice import BoxOfficeMovie


class TestMovieMatcher:
    """Test MovieMatcher class."""
    
    @pytest.fixture
    def matcher(self):
        """Create MovieMatcher instance."""
        return MovieMatcher(min_confidence=0.8)
    
    @pytest.fixture
    def sample_radarr_movies(self) -> List[RadarrMovie]:
        """Create sample Radarr movies for testing."""
        return [
            RadarrMovie(
                id=1,
                title="The Dark Knight",
                tmdbId=155,
                year=2008,
                hasFile=True
            ),
            RadarrMovie(
                id=2,
                title="Jurassic World: Rebirth",
                tmdbId=1234821,
                year=2025,
                hasFile=False
            ),
            RadarrMovie(
                id=3,
                title="Inception",
                tmdbId=27205,
                year=2010,
                hasFile=True
            ),
            RadarrMovie(
                id=4,
                title="Spider-Man: No Way Home",
                tmdbId=634649,
                year=2021,
                hasFile=True
            ),
            RadarrMovie(
                id=5,
                title="Avatar: The Way of Water",
                tmdbId=76600,
                year=2022,
                hasFile=False
            ),
        ]
    
    def test_normalize_title(self, matcher):
        """Test title normalization."""
        assert matcher.normalize_title("The Dark Knight") == "the dark knight"
        assert matcher.normalize_title("Spider-Man: No Way Home") == "spiderman no way home"
        assert matcher.normalize_title("Avatar - The Way of Water") == "avatar the way of water"
        assert matcher.normalize_title("  Multiple   Spaces  ") == "multiple spaces"
    
    def test_remove_articles(self, matcher):
        """Test article removal."""
        assert matcher.remove_articles("The Dark Knight") == "dark knight"
        assert matcher.remove_articles("A Beautiful Mind") == "beautiful mind"
        assert matcher.remove_articles("An Unexpected Journey") == "unexpected journey"
        assert matcher.remove_articles("Le Fabuleux Destin") == "fabuleux destin"
        assert matcher.remove_articles("Dark Knight") == "dark knight"
    
    def test_get_base_title(self, matcher):
        """Test base title extraction."""
        assert matcher.get_base_title("Spider-Man: No Way Home") == "Spider-Man"
        assert matcher.get_base_title("Avatar: The Way of Water") == "Avatar"
        assert matcher.get_base_title("Fast & Furious 9") == "Fast & Furious"
        assert matcher.get_base_title("Rocky IV") == "Rocky"
        assert matcher.get_base_title("The Matrix") == "The Matrix"
    
    def test_extract_year(self, matcher):
        """Test year extraction."""
        assert matcher.extract_year("The Dark Knight (2008)") == 2008
        assert matcher.extract_year("Inception (2010) Director's Cut") == 2010
        assert matcher.extract_year("No Year Here") is None
        assert matcher.extract_year("(1999)") == 1999
    
    def test_calculate_similarity(self, matcher):
        """Test similarity calculation."""
        score = matcher.calculate_similarity("The Dark Knight", "The Dark Knight")
        assert score == 1.0
        
        score = matcher.calculate_similarity("The Dark Knight", "Dark Knight")
        assert 0.7 < score < 0.9
        
        score = matcher.calculate_similarity("Spider-Man", "Spiderman")
        assert score > 0.8
        
        score = matcher.calculate_similarity("Avatar", "Titanic")
        assert score < 0.5
    
    def test_exact_match(self, matcher, sample_radarr_movies):
        """Test exact title matching."""
        result = matcher.match_single("The Dark Knight", sample_radarr_movies)
        
        assert result.is_matched
        assert result.radarr_movie.id == 1
        assert result.confidence == 1.0
        assert result.match_method == "exact"
    
    def test_normalized_match(self, matcher, sample_radarr_movies):
        """Test normalized title matching."""
        # Test with different formatting
        result = matcher.match_single("spider-man: no way home", sample_radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.id == 4
        assert result.confidence >= 0.95
        assert result.match_method == "normalized"
        
        # Test without article
        result = matcher.match_single("Dark Knight", sample_radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.id == 1
    
    def test_fuzzy_match(self, matcher, sample_radarr_movies):
        """Test fuzzy matching."""
        # Slightly different title
        result = matcher.match_single("Spiderman No Way Home", sample_radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.id == 4
        assert result.confidence >= 0.8
    
    def test_special_case_colon_handling(self, matcher, sample_radarr_movies):
        """Test special case: colon vs no colon."""
        # "Jurassic World: Rebirth" vs "Jurassic World Rebirth"
        result = matcher.match_single("Jurassic World Rebirth", sample_radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.id == 2
        
        # "Avatar: The Way of Water" vs "Avatar The Way of Water"
        result = matcher.match_single("Avatar The Way of Water", sample_radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.id == 5
    
    def test_no_match(self, matcher, sample_radarr_movies):
        """Test when no match is found."""
        result = matcher.match_single("Some Unknown Movie", sample_radarr_movies)
        
        assert not result.is_matched
        assert result.radarr_movie is None
        assert result.confidence == 0.0
        assert result.match_method == "none"
    
    def test_match_batch(self, matcher, sample_radarr_movies):
        """Test batch matching."""
        box_office_movies = [
            BoxOfficeMovie(rank=1, title="The Dark Knight"),
            BoxOfficeMovie(rank=2, title="Jurassic World Rebirth"),  # No colon
            BoxOfficeMovie(rank=3, title="Unknown Movie"),
            BoxOfficeMovie(rank=4, title="Inception"),
        ]
        
        results = matcher.match_batch(box_office_movies, sample_radarr_movies)
        
        assert len(results) == 4
        
        # Check specific matches
        assert results[0].is_matched  # The Dark Knight
        assert results[0].radarr_movie.id == 1
        
        assert results[1].is_matched  # Jurassic World
        assert results[1].radarr_movie.id == 2
        
        assert not results[2].is_matched  # Unknown Movie
        
        assert results[3].is_matched  # Inception
        assert results[3].radarr_movie.id == 3
    
    def test_build_movie_index(self, matcher, sample_radarr_movies):
        """Test movie index building."""
        matcher.build_movie_index(sample_radarr_movies)
        
        # Check that cache is populated
        assert len(matcher._movie_cache) > 0
        
        # Check that movies can be found by different keys
        assert "the dark knight" in matcher._movie_cache
        assert "dark knight" in matcher._movie_cache  # Without article
        assert "inception" in matcher._movie_cache
    
    def test_confidence_threshold(self):
        """Test confidence threshold handling."""
        # High threshold
        high_matcher = MovieMatcher(min_confidence=0.95)
        
        # Low threshold
        low_matcher = MovieMatcher(min_confidence=0.5)
        
        movies = [
            RadarrMovie(
                id=1,
                title="The Amazing Spider-Man",
                tmdbId=1930,
                year=2012
            )
        ]
        
        # With high threshold, fuzzy match might not pass
        result_high = high_matcher.match_single("Spider Man", movies)
        
        # With low threshold, fuzzy match should pass
        result_low = low_matcher.match_single("Spider Man", movies)
        
        # Low threshold should be more permissive
        assert result_low.confidence >= result_high.confidence
    
    def test_year_bonus(self, matcher):
        """Test year matching bonus in fuzzy matching."""
        movies = [
            RadarrMovie(
                id=1,
                title="King Kong",
                tmdbId=254,
                year=2005
            ),
            RadarrMovie(
                id=2,
                title="King Kong",
                tmdbId=857,
                year=1976
            ),
        ]
        
        # Should match 2005 version due to year
        result = matcher.match_single("King Kong (2005)", movies)
        assert result.is_matched
        assert result.radarr_movie.id == 1
        assert result.radarr_movie.year == 2005