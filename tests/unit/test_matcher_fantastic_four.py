"""Test case for the Fantastic Four matching bug."""

import pytest

from src.core.boxoffice import BoxOfficeMovie
from src.core.matcher import MovieMatcher
from src.core.models import MovieStatus
from src.core.radarr import RadarrMovie


class TestFantasticFourBug:
    """Test the specific bug where 'The Fantastic Four' doesn't match 'The Fantastic 4'."""
    
    def test_number_word_conversion_in_titles(self):
        """Test that titles with numbers spelled out match titles with digits."""
        # Use the actual default confidence threshold
        matcher = MovieMatcher()  # Default is 0.95
        
        # Create Radarr movies that simulate the actual scenario
        radarr_movies = [
            RadarrMovie(
                id=617126,
                title="The Fantastic 4: First Steps",  # Uses number 4
                tmdbId=617126,
                year=2025,
                status=MovieStatus.ANNOUNCED,
                hasFile=False,
            ),
            RadarrMovie(
                id=1516738,
                title="Marvel Studios' The Fantastic Four: First Steps - World Premiere",
                tmdbId=1516738,
                year=2025,
                status=MovieStatus.ANNOUNCED,
                hasFile=False,
            ),
        ]
        
        matcher.build_movie_index(radarr_movies)
        
        # Test matching "The Fantastic Four: First Steps" from Box Office Mojo
        result = matcher.match_single("The Fantastic Four: First Steps", radarr_movies)
        
        # Debug output
        print(f"Match result: is_matched={result.is_matched}, method={result.match_method}, confidence={result.confidence}")
        if result.is_matched:
            print(f"Matched movie: id={result.radarr_movie.id}, title={result.radarr_movie.title}")
        
        # This should match the first movie (The Fantastic 4: First Steps)
        assert result.is_matched, f"Failed to match 'The Fantastic Four: First Steps'. Method: {result.match_method}, Confidence: {result.confidence}"
        assert result.radarr_movie.id == 617126, f"Wrong movie matched: got id={result.radarr_movie.id}, expected 617126"
        assert result.radarr_movie.title == "The Fantastic 4: First Steps"
        
    def test_various_number_word_conversions(self):
        """Test various number-to-word conversions in titles."""
        matcher = MovieMatcher(min_confidence=0.8)
        
        radarr_movies = [
            RadarrMovie(id=1, title="2 Fast 2 Furious", tmdbId=1001, year=2003),
            RadarrMovie(id=2, title="Ocean's 11", tmdbId=1002, year=2001),
            RadarrMovie(id=3, title="The Magnificent 7", tmdbId=1003, year=2016),
            RadarrMovie(id=4, title="9 to 5", tmdbId=1004, year=1980),
            RadarrMovie(id=5, title="3:10 to Yuma", tmdbId=1005, year=2007),
            RadarrMovie(id=6, title="Fantastic 4", tmdbId=1006, year=2015),
        ]
        
        matcher.build_movie_index(radarr_movies)
        
        # Test word-to-number conversions
        test_cases = [
            ("Two Fast Two Furious", "2 Fast 2 Furious"),
            ("Ocean's Eleven", "Ocean's 11"),
            ("The Magnificent Seven", "The Magnificent 7"),
            ("Nine to Five", "9 to 5"),
            # Note: "3:10 to Yuma" would likely appear as "3:10 to Yuma" in Box Office Mojo,
            # not "Three Ten to Yuma", so we'll test a more realistic case
            ("Fantastic Four", "Fantastic 4"),
        ]
        
        for box_office_title, expected_radarr_title in test_cases:
            result = matcher.match_single(box_office_title, radarr_movies)
            assert result.is_matched, f"Failed to match '{box_office_title}' to '{expected_radarr_title}'"
            assert result.radarr_movie.title == expected_radarr_title
            
    def test_partial_title_with_number_word_conversion(self):
        """Test that partial titles with number/word differences still match."""
        matcher = MovieMatcher(min_confidence=0.8)
        
        radarr_movies = [
            RadarrMovie(
                id=1,
                title="The Fantastic 4",  # Shorter title with number
                tmdbId=1001,
                year=2025,
            ),
            RadarrMovie(
                id=2,
                title="4 Brothers",  # Number at start
                tmdbId=1002,
                year=2005,
            ),
        ]
        
        matcher.build_movie_index(radarr_movies)
        
        # Should match even with extra subtitle
        result = matcher.match_single("The Fantastic Four: Rise of the Silver Surfer", radarr_movies)
        assert result.is_matched
        assert "Fantastic" in result.radarr_movie.title
        
        # Should match "Four Brothers" to "4 Brothers"
        result = matcher.match_single("Four Brothers", radarr_movies)
        assert result.is_matched
        assert result.radarr_movie.title == "4 Brothers"