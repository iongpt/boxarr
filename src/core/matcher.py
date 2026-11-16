"""Movie matching algorithms for finding Radarr movies from box office titles."""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple

from ..utils.logger import get_logger
from .boxoffice import BoxOfficeMovie
from .radarr import RadarrMovie

logger = get_logger(__name__)

# Combining accents of Latin, Greek and Cyrillic letters after NFKD. Marks of
# other scripts (Japanese dakuten, Devanagari virama) change the word: kept.
_LATIN_ACCENTS = re.compile("[̀-ͯ]")
# Hyphen, the Unicode hyphens and dashes.
_HYPHENS_AND_DASHES = re.compile("[-‐-―]")


def _same_words(title: str) -> str:
    """``title`` spelled loosely for a whole-title comparison: accents
    dropped from Latin letters, "&" read as "and", a hyphen or dash read as
    a space between words."""
    loose = _LATIN_ACCENTS.sub("", unicodedata.normalize("NFKD", title))
    loose = re.sub(r"\s*&\s*", " and ", loose)
    return _HYPHENS_AND_DASHES.sub(" ", loose)


def _imdb_key(imdb_id: Optional[str]) -> str:
    """An IMDb id for comparison: case and whitespace insensitive, "" if none."""
    return (imdb_id or "").strip().lower()


def _imdb_conflict(chart_id: str, library_imdb_id: Optional[str]) -> bool:
    """Whether a library film's IMDb id is known and not ``chart_id`` (a key)."""
    library_id = _imdb_key(library_imdb_id)
    return bool(chart_id and library_id and library_id != chart_id)


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

    # Common subtitle patterns to remove for matching. A hyphen or an en dash
    # that joins words ("Spider-Man", "X-Men", "Spider–Man", "1914–1918") is
    # part of the title, never a subtitle separator: either one cuts only with
    # whitespace on both sides ("Spider-Man - No Way Home", "Avatar – Aufbruch
    # nach Pandora"). Two hyphens or an em dash cut, spaced or not.
    SUBTITLE_PATTERNS = [
        r"\s*:\s*.*$",  # Remove everything after colon
        r"\s+(?:-+|–)\s+.*$",  # Remove everything after a spaced hyphen/en dash
        r"\s*(?:--+|—).*$",  # Remove everything after "--" or an em dash
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

    # Number to word mappings for common cases (only cardinal numbers, not ordinals)
    NUMBER_WORDS = {
        "1": "one",
        "2": "two",
        "3": "three",
        "4": "four",
        "5": "five",
        "6": "six",
        "7": "seven",
        "8": "eight",
        "9": "nine",
        "10": "ten",
        "11": "eleven",
        "12": "twelve",
    }

    # Reverse mapping: word to number (only cardinal numbers)
    WORD_TO_NUMBER = {
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
        "eleven": "11",
        "twelve": "12",
    }

    def __init__(self, min_confidence: float = 0.95):
        """
        Initialize movie matcher.

        Args:
            min_confidence: Minimum confidence threshold for matches
        """
        self.min_confidence = min_confidence
        self._movie_cache: Dict[str, RadarrMovie] = {}
        self._imdb_index: Dict[str, RadarrMovie] = {}

    def build_movie_index(self, movies: List[RadarrMovie]) -> None:
        """
        Build search index from Radarr movies.

        Args:
            movies: List of Radarr movies
        """
        self._movie_cache.clear()
        self._imdb_index.clear()

        for movie in movies:
            if movie.imdbId:
                self._imdb_index[movie.imdbId] = movie

        def _is_sequel(title: str) -> bool:
            # Has trailing number or roman numeral
            has_number = re.search(r"\s+\d+$", title) is not None
            if has_number:
                return True
            # Check for Roman numerals at the end
            words = title.strip().split()
            if words and words[-1].upper() in self.ROMAN_NUMERALS:
                return True
            return False

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
                key = base_title.lower()
                existing = self._movie_cache.get(key)
                if existing is None:
                    self._movie_cache[key] = movie
                else:
                    # Prefer non-sequel for the base title mapping
                    if _is_sequel(existing.title) and not _is_sequel(movie.title):
                        self._movie_cache[key] = movie

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

    def _sequel_marker(self, title: str) -> Optional[int]:
        """
        Extract an explicit trailing sequel marker as an integer.

        Recognizes a trailing arabic number, a trailing Roman numeral, or a
        trailing "Part N" (arabic, Roman, or number word). Returns None when
        the title carries no such marker.

        Args:
            title: Movie title

        Returns:
            Sequel number or None
        """
        stripped = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()

        part_match = re.search(r"\bpart\s+(\w+)$", stripped, flags=re.IGNORECASE)
        if part_match:
            token = part_match.group(1)
            if token.isdigit():
                return int(token)
            if token.upper() in self.ROMAN_NUMERALS:
                return self.ROMAN_NUMERALS[token.upper()]
            if token.lower() in self.WORD_TO_NUMBER:
                return int(self.WORD_TO_NUMBER[token.lower()])
            return None

        num_match = re.search(r"\s+(\d+)$", stripped)
        if num_match:
            return int(num_match.group(1))

        words = stripped.split()
        if len(words) >= 2 and words[-1].upper() in self.ROMAN_NUMERALS:
            return self.ROMAN_NUMERALS[words[-1].upper()]

        return None

    def _base_match_blocked(self, query_title: str, candidate: RadarrMovie) -> bool:
        """
        Decide whether a base-title/fuzzy match must be refused.

        A query carrying an explicit sequel marker (e.g. "Gladiator II",
        "Sonic the Hedgehog 3") must not collapse, via the digit/numeral
        stripped base title, onto a library entry that lacks an equivalent
        marker. Equivalence across marker forms is preserved ("Gladiator II"
        still matches "Gladiator 2"), and a release-year match within one year
        corroborates that the two titles are the same film.

        Only the query-has-marker direction is guarded: a plain query is left
        free to match a numbered library entry, since trailing numbers often
        belong to the brand ("The Fantastic 4", "Ocean's 11") rather than a
        sequel.

        Args:
            query_title: Box office title being matched
            candidate: Candidate Radarr movie

        Returns:
            True if the match must be refused
        """
        query_marker = self._sequel_marker(query_title)
        if query_marker is None:
            return False

        candidate_marker = self._sequel_marker(candidate.title)
        if candidate_marker == query_marker:
            return False

        query_year = self.extract_year(query_title)
        if (
            query_year is not None
            and candidate.year is not None
            and abs(candidate.year - query_year) <= 1
        ):
            return False

        return True

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

    def convert_numbers_to_words(self, title: str) -> str:
        """
        Convert numbers in title to word equivalents.

        Args:
            title: Movie title

        Returns:
            Title with numbers converted to words
        """
        import re

        result = title

        # Convert standalone numbers to words
        for num, word in self.NUMBER_WORDS.items():
            # Match number as a whole word (with word boundaries)
            pattern = r"\b" + re.escape(num) + r"\b"
            if re.search(pattern, result):
                result = re.sub(pattern, word, result, flags=re.IGNORECASE)

        return result

    def convert_words_to_numbers(self, title: str) -> str:
        """
        Convert number words in title to digit equivalents.

        Args:
            title: Movie title

        Returns:
            Title with words converted to numbers
        """
        import re

        result = title

        # Convert word numbers to digits
        for word, num in self.WORD_TO_NUMBER.items():
            # Match word as a whole word (with word boundaries)
            pattern = r"\b" + re.escape(word) + r"\b"
            if re.search(pattern, result, re.IGNORECASE):
                result = re.sub(pattern, num, result, flags=re.IGNORECASE)

        return result

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

        # Try special cases (sequels, remakes, etc.)
        result = self._try_special_cases(box_office_title, radarr_movies)
        if result:
            return MatchResult(
                box_office_movie=box_office_movie,
                radarr_movie=result,
                confidence=0.85,
                match_method="special",
            )

        # Try normalized match (including number conversions)
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

        # No match found
        return MatchResult(
            box_office_movie=box_office_movie,
            radarr_movie=None,
            confidence=0.0,
            match_method="none",
        )

    def _try_imdb_match(self, imdb_id: Optional[str]) -> Optional[RadarrMovie]:
        """Try matching by IMDb ID."""
        if not imdb_id:
            return None
        return self._imdb_index.get(imdb_id)

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

        # Try replacing trailing Roman numerals with numbers and re-check
        title_upper = title.upper()
        for numeral in sorted(self.ROMAN_NUMERALS.keys(), key=len, reverse=True):
            if title_upper.endswith(f" {numeral}"):
                replaced = re.sub(
                    rf"\b{numeral}\b",
                    str(self.ROMAN_NUMERALS[numeral]),
                    title_upper,
                    flags=re.IGNORECASE,
                )
                alt_norm = self.normalize_title(replaced)
                if alt_norm in self._movie_cache:
                    return self._movie_cache[alt_norm]

        # Try base title
        base_title = self.get_base_title(title)
        if base_title.lower() in self._movie_cache:
            candidate = self._movie_cache[base_title.lower()]
            if not self._base_match_blocked(title, candidate):
                return candidate

        # Try converting numbers to words and vice versa
        # This handles cases like "The Fantastic Four" vs "The Fantastic 4"
        title_with_numbers = self.convert_words_to_numbers(title)
        if title_with_numbers != title:
            # Try exact match with converted title
            if title_with_numbers.lower() in self._movie_cache:
                return self._movie_cache[title_with_numbers.lower()]

            # Try normalized match with converted title
            normalized_numbers = self.normalize_title(title_with_numbers)
            if normalized_numbers in self._movie_cache:
                return self._movie_cache[normalized_numbers]

        title_with_words = self.convert_numbers_to_words(title)
        if title_with_words != title:
            # Try exact match with converted title
            if title_with_words.lower() in self._movie_cache:
                return self._movie_cache[title_with_words.lower()]

            # Try normalized match with converted title
            normalized_words = self.normalize_title(title_with_words)
            if normalized_words in self._movie_cache:
                return self._movie_cache[normalized_words]

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
            # Skip candidates the query's sequel marker rules out, so a
            # digit/numeral stripped base score cannot resurrect a wrong film.
            if self._base_match_blocked(title, movie):
                continue

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
                # Prefer exact/normalized equality to sequel title
                norm_target = self.normalize_title(title_with_number)
                for m in radarr_movies:
                    if self.normalize_title(m.title) == norm_target:
                        return m
                result = self._try_normalized_match(title_with_number)
                if result:
                    return result

        return None

    def _title_match(
        self, box_movie: BoxOfficeMovie, radarr_movies: List[RadarrMovie]
    ) -> MatchResult:
        """
        Title-match a chart entry, never to a film with another IMDb id.

        The IMDb id is a veto, not only a fast path: a new sequel, spin-off or
        remake is not in the library yet, so its id misses the index, and the
        lossy title fallbacks would find the older film ("Wicked: For Good" ->
        "Wicked", "Lilo & Stitch" 2025 -> 2002). When they land on a library
        film whose IMDb id differs from the chart entry's, only that film is
        skipped: a film of the same full title without an IMDb id (or with
        the same id, spelled differently) is taken instead - the new film
        Radarr lists before TMDB has its id. The lossy fallbacks are not run
        again over the rest of the library, where they would only find
        another film of the franchise ("Avatar: Fire and Ash" -> "Avatar:
        The Way of Water").

        A chart entry with an IMDb id is matched only by that id or by its
        whole title. A library film with the id is found by the id, so the
        title match can only add a film without one, and the lossy fallbacks
        (base title, the special cases, numeral spellings, fuzzy) must not
        pick it: when the original film is not in the library they link
        "Avatar: Fire and Ash" to an "Avatar" or "Avatar 4" without an id.
        The title match stands when its film has the chart's id, spelled
        differently, or has no id and the chart's whole title: the same
        words, give or take the leading article, case, punctuation, accents,
        "&" for "and" and a hyphen for a space (``_full_title_keys``), so a
        lossy step that found the same film spelled differently is kept with
        its method and confidence. Otherwise the whole-title replacement
        above is tried, so a lossy pick does not hide a film of the entry's
        whole title elsewhere in the library: for the entry "The Smurfs",
        the base title of "The Smurfs 2" is found before "Smurfs". Without a
        chart id the result is the plain title match, as before.

        The shared index (a scheduler reuses one matcher) is never changed.
        """
        result = self.match_single(box_movie.title, radarr_movies)
        result.box_office_movie = box_movie
        candidate = result.radarr_movie
        chart_id = _imdb_key(box_movie.imdb_id)
        if candidate is None or not chart_id:
            return result
        if _imdb_conflict(chart_id, candidate.imdbId):
            logger.info(
                f"Not matching '{box_movie.title}' ({box_movie.imdb_id}) to "
                f"'{candidate.title}' ({candidate.imdbId}): different IMDb ids"
            )
        elif _imdb_key(candidate.imdbId) == chart_id or self._is_whole_title(
            box_movie.title, candidate.title
        ):
            return result
        else:
            logger.info(
                f"Not matching '{box_movie.title}' ({box_movie.imdb_id}) to "
                f"'{candidate.title}', which has no IMDb id: an entry with an "
                "IMDb id is matched only by it or by its whole title"
            )
        return self._same_title_match(box_movie, radarr_movies)

    def _same_title_match(
        self, box_movie: BoxOfficeMovie, radarr_movies: List[RadarrMovie]
    ) -> MatchResult:
        """
        Match a library film of the chart entry's full title, if one is allowed.

        Only films whose IMDb id does not differ from the chart entry's are
        considered, and only by their whole title - exactly, or with the same
        words as ``_full_title_keys`` compares them - never by a base title,
        a subtitle or fuzzily.
        """
        chart_id = _imdb_key(box_movie.imdb_id)
        title = box_movie.title
        keys = self._full_title_keys(title)
        for movie in radarr_movies:
            if _imdb_conflict(chart_id, movie.imdbId):
                continue
            if movie.title.lower() == title.lower():
                return MatchResult(box_movie, movie, 1.0, "exact")
            if keys & self._full_title_keys(movie.title):
                return MatchResult(box_movie, movie, 0.95, "normalized")
        return MatchResult(box_office_movie=box_movie)

    def _is_whole_title(self, title: str, movie_title: str) -> bool:
        """Whether ``movie_title`` is ``title`` as ``_same_title_match``
        compares them: the same words, give or take the leading article.
        Titles equal but for case share their normalized key."""
        return bool(self._full_title_keys(title) & self._full_title_keys(movie_title))

    def _full_title_keys(self, title: str) -> Set[str]:
        """
        A title's whole-title forms: normalized, and without its article.

        Each form is also keyed with its words spelled loosely: accents
        dropped from Latin letters, "&" read as "and", and a hyphen or dash
        read as a space. normalize_title deletes a hyphen and so glues the
        words it joins ("Half-Blood" -> "halfblood"), which the same title
        written with a space never equals. Two titles sharing a key have the
        same words in the same order ("Harry Potter and the Half-Blood
        Prince" / "Harry Potter and the Half Blood Prince", "De Gaulle:
        Résistance" / "De Gaulle: Resistance", "Dungeons & Dragons" /
        "Dungeons and Dragons"); no word is added, dropped or renumbered.
        """
        keys = set()
        for form in (title, self.remove_articles(title)):
            keys.add(self.normalize_title(form))
            keys.add(self.normalize_title(_same_words(form)))
        return keys

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
            # Try IMDb match first (language-agnostic)
            imdb_match = self._try_imdb_match(box_movie.imdb_id)
            if imdb_match:
                match_result = MatchResult(
                    box_office_movie=box_movie,
                    radarr_movie=imdb_match,
                    confidence=1.0,
                    match_method="imdb_id",
                )
            else:
                match_result = self._title_match(box_movie, radarr_movies)
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
        # Build index if needed
        if not self._movie_cache:
            self.build_movie_index(radarr_movies)

        # Try IMDb match first (language-agnostic)
        imdb_match = self._try_imdb_match(box_office_movie.imdb_id)
        if imdb_match:
            return MatchResult(
                box_office_movie=box_office_movie,
                radarr_movie=imdb_match,
                confidence=1.0,
                match_method="imdb_id",
            )

        # Fall back to title matching, never to a film with another IMDb id
        return self._title_match(box_office_movie, radarr_movies)

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
