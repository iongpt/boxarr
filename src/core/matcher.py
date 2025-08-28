"""Movie matching algorithms for finding Radarr movies from box office titles."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from ..utils.logger import get_logger
from .boxoffice import BoxOfficeMovie
from .radarr import RadarrMovie

logger = get_logger(__name__)


@dataclass
class MatchResult:
    """Result of movie matching attempt."""

    box_office_movie: BoxOfficeMovie
    radarr_movie: Optional[RadarrMovie] = None
    confidence: float = 0.0
    match_method: str = "none"

    @property
    def is_matched(self) -> bool:
        """Check if movie was successfully matched."""
        return self.radarr_movie is not None


class MovieMatcher:
    """Service for matching box office titles with Radarr movies."""

    # Common subtitle patterns to remove for matching
    SUBTITLE_PATTERNS = [
        r"\s*:\s*.*$",  # Remove everything after colon
        r"\s*-\s*.*$",  # Remove everything after dash
        r"\s*\(.*?\)",  # Remove parenthetical content
        r"\s*\[.*?\]",  # Remove bracketed content
    ]

    # Roman numerals for sequel detection
    ROMAN_NUMERALS = {
        "I": 1,
        "II": 2,
        "III": 3,
        "IV": 4,
        "V": 5,
        "VI": 6,
        "VII": 7,
        "VIII": 8,
        "IX": 9,
        "X": 10,
    }

    def __init__(self, min_confidence: float = 0.8):
        """
        Initialize movie matcher.

        Args:
            min_confidence: Minimum confidence threshold for matches
        """
        self.min_confidence = min_confidence
        self._movie_cache: Dict[str, RadarrMovie] = {}

    def build_movie_index(self, movies: List[RadarrMovie]) -> None:
        """
        Build search index from Radarr movies.

        Args:
            movies: List of Radarr movies
        """
        self._movie_cache.clear()

        for movie in movies:
            # Index by exact title
            self._movie_cache[movie.title.lower()] = movie

            # Index by normalized title
            normalized = self.normalize_title(movie.title)
            self._movie_cache[normalized] = movie

            # Index by title without articles
            no_articles = self.remove_articles(movie.title)
            self._movie_cache[no_articles.lower()] = movie

            # Index by title without subtitle
            base_title = self.get_base_title(movie.title)
            if base_title != movie.title:
                self._movie_cache[base_title.lower()] = movie

        logger.info(f"Built movie index with {len(movies)} movies")

    def normalize_title(self, title: str) -> str:
        """
        Normalize title for matching.

        Args:
            title: Movie title

        Returns:
            Normalized title
        """
        # Remove non-alphanumeric characters
        normalized = re.sub(r"[^\w\s]", "", title.lower())
        # Collapse multiple spaces
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def remove_articles(self, title: str) -> str:
        """
        Remove leading articles from title.

        Args:
            title: Movie title

        Returns:
            Title without articles
        """
        articles = ["the", "a", "an", "le", "la", "les", "el", "los", "las"]
        words = title.lower().split()

        if words and words[0] in articles:
            return " ".join(words[1:])

        return title.lower()

    def get_base_title(self, title: str) -> str:
        """
        Get base title without subtitle or sequel indicators.

        Args:
            title: Movie title

        Returns:
            Base title
        """
        # Remove common subtitle patterns
        base = title
        for pattern in self.SUBTITLE_PATTERNS:
            base = re.sub(pattern, "", base, flags=re.IGNORECASE)

        # Remove sequel numbers
        base = re.sub(r"\s+\d+$", "", base)

        # Remove Roman numerals
        words = base.split()
        if words and words[-1].upper() in self.ROMAN_NUMERALS:
            base = " ".join(words[:-1])

        return base.strip()

    def extract_year(self, title: str) -> Optional[int]:
        """
        Extract year from title if present.

        Args:
            title: Movie title

        Returns:
            Year or None
        """
        match = re.search(r"\((\d{4})\)", title)
        return int(match.group(1)) if match else None

    def calculate_similarity(self, str1: str, str2: str) -> float:
        """
        Calculate similarity score between two strings.

        Args:
            str1: First string
            str2: Second string

        Returns:
            Similarity score between 0 and 1
        """
        return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()

    def match_single(
        self, box_office_title: str, radarr_movies: List[RadarrMovie]
    ) -> MatchResult:
        """
        Match a single box office title with Radarr movies.

        Args:
            box_office_title: Title from box office
            radarr_movies: List of Radarr movies

        Returns:
            MatchResult object
        """
        # Create box office movie object
        box_office_movie = BoxOfficeMovie(rank=0, title=box_office_title)

        # Build index if needed
        if not self._movie_cache:
            self.build_movie_index(radarr_movies)

        # Try exact match
        result = self._try_exact_match(box_office_title)
        if result:
            return MatchResult(
                box_office_movie=box_office_movie,
                radarr_movie=result,
                confidence=1.0,
                match_method="exact",
            )

        # Try normalized match
        result = self._try_normalized_match(box_office_title)
        if result:
            return MatchResult(
                box_office_movie=box_office_movie,
                radarr_movie=result,
                confidence=0.95,
                match_method="normalized",
            )

        # Try fuzzy matching
        result, confidence = self._try_fuzzy_match(box_office_title, radarr_movies)
        if result and confidence >= self.min_confidence:
            return MatchResult(
                box_office_movie=box_office_movie,
                radarr_movie=result,
                confidence=confidence,
                match_method="fuzzy",
            )

        # Try special cases (sequels, remakes, etc.)
        result = self._try_special_cases(box_office_title, radarr_movies)
        if result:
            return MatchResult(
                box_office_movie=box_office_movie,
                radarr_movie=result,
                confidence=0.85,
                match_method="special",
            )

        # No match found
        return MatchResult(
            box_office_movie=box_office_movie,
            radarr_movie=None,
            confidence=0.0,
            match_method="none",
        )

    def _try_exact_match(self, title: str) -> Optional[RadarrMovie]:
        """Try exact title match."""
        return self._movie_cache.get(title.lower())

    def _try_normalized_match(self, title: str) -> Optional[RadarrMovie]:
        """Try normalized title match."""
        normalized = self.normalize_title(title)

        # Try normalized title
        if normalized in self._movie_cache:
            return self._movie_cache[normalized]

        # Try without articles
        no_articles = self.remove_articles(title)
        if no_articles in self._movie_cache:
            return self._movie_cache[no_articles]

        # Try base title
        base_title = self.get_base_title(title)
        if base_title.lower() in self._movie_cache:
            return self._movie_cache[base_title.lower()]

        return None

    def _try_fuzzy_match(
        self, title: str, radarr_movies: List[RadarrMovie]
    ) -> Tuple[Optional[RadarrMovie], float]:
        """
        Try fuzzy string matching.

        Returns:
            Tuple of (matched movie, confidence score)
        """
        best_match = None
        best_score = 0.0

        normalized_title = self.normalize_title(title)

        for movie in radarr_movies:
            # Calculate various similarity scores
            exact_score = self.calculate_similarity(title, movie.title)
            normalized_score = self.calculate_similarity(
                normalized_title, self.normalize_title(movie.title)
            )
            base_score = self.calculate_similarity(
                self.get_base_title(title), self.get_base_title(movie.title)
            )

            # Take the highest score
            score = max(exact_score, normalized_score, base_score)

            # Bonus for year match
            box_year = self.extract_year(title)
            if box_year and movie.year == box_year:
                score += 0.1

            if score > best_score:
                best_score = score
                best_match = movie

        return best_match, best_score

    def _try_special_cases(
        self, title: str, radarr_movies: List[RadarrMovie]
    ) -> Optional[RadarrMovie]:
        """
        Handle special cases like sequels with different naming.

        Args:
            title: Box office title
            radarr_movies: List of Radarr movies

        Returns:
            Matched movie or None
        """
        # Handle "Movie: Subtitle" vs "Movie Subtitle"
        if ":" in title:
            no_colon = title.replace(":", "").replace("  ", " ")
            result = self._try_normalized_match(no_colon)
            if result:
                return result

        # Handle year in title
        year_match = re.search(r"\((\d{4})\)", title)
        if year_match:
            year = int(year_match.group(1))
            title_no_year = re.sub(r"\s*\(\d{4}\)", "", title)

            for movie in radarr_movies:
                if (
                    movie.year == year
                    and self.calculate_similarity(title_no_year, movie.title) > 0.8
                ):
                    return movie

        # Handle Roman numeral sequels
        for numeral, value in self.ROMAN_NUMERALS.items():
            if f" {numeral}" in title.upper() or title.upper().endswith(numeral):
                # Try replacing with number
                title_with_number = re.sub(
                    rf"\b{numeral}\b", str(value), title, flags=re.IGNORECASE
                )
                result = self._try_normalized_match(title_with_number)
                if result:
                    return result

        return None

    def match_batch(
        self, box_office_movies: List[BoxOfficeMovie], radarr_movies: List[RadarrMovie]
    ) -> List[MatchResult]:
        """
        Match multiple box office movies with Radarr library.

        Args:
            box_office_movies: List of box office movies
            radarr_movies: List of Radarr movies

        Returns:
            List of MatchResult objects
        """
        # Build index once for efficiency
        self.build_movie_index(radarr_movies)

        results = []
        for box_movie in box_office_movies:
            match_result = self.match_single(box_movie.title, radarr_movies)
            # Update the box office movie in the result
            match_result.box_office_movie = box_movie
            results.append(match_result)

            if match_result.is_matched:
                logger.debug(
                    f"Matched '{box_movie.title}' to '{match_result.radarr_movie.title}' "
                    f"(confidence: {match_result.confidence:.2f}, method: {match_result.match_method})"
                )
            else:
                logger.debug(f"No match found for '{box_movie.title}'")

        matched_count = sum(1 for r in results if r.is_matched)
        logger.info(
            f"Matched {matched_count}/{len(box_office_movies)} box office movies"
        )

        return results

    def match_movie(
        self, box_office_movie: BoxOfficeMovie, radarr_movies: List[RadarrMovie]
    ) -> MatchResult:
        """
        Alias for match_single to maintain compatibility with routes.

        Args:
            box_office_movie: Box office movie to match
            radarr_movies: List of Radarr movies

        Returns:
            MatchResult object
        """
        return self.match_single(box_office_movie, radarr_movies)

    def match_movies(
        self, box_office_movies: List[BoxOfficeMovie], radarr_movies: List[RadarrMovie]
    ) -> List[MatchResult]:
        """
        Alias for match_batch to maintain compatibility with routes.

        Args:
            box_office_movies: List of box office movies
            radarr_movies: List of Radarr movies

        Returns:
            List of MatchResult objects
        """
        return self.match_batch(box_office_movies, radarr_movies)
