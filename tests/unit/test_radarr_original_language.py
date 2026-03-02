"""Unit tests for RadarrMovie original_language extraction."""

from src.core.radarr import RadarrMovie, RadarrService


class TestParseMovieOriginalLanguage:
    """Test _parse_movie() extracts originalLanguage correctly."""

    def _make_service(self):
        return RadarrService(url="http://localhost:7878", api_key="test_key")

    def _base_movie_data(self, **overrides):
        data = {
            "id": 1,
            "title": "Test Movie",
            "tmdbId": 12345,
            "year": 2025,
            "status": "released",
            "hasFile": False,
            "monitored": True,
            "isAvailable": False,
            "images": [],
            "genres": ["Action"],
        }
        data.update(overrides)
        return data

    def test_parse_movie_with_english_language(self):
        """originalLanguage dict with name 'English' is extracted."""
        service = self._make_service()
        data = self._base_movie_data(originalLanguage={"id": 1, "name": "English"})
        movie = service._parse_movie(data)
        assert movie.original_language == "English"

    def test_parse_movie_with_hindi_language(self):
        """originalLanguage dict with name 'Hindi' is extracted."""
        service = self._make_service()
        data = self._base_movie_data(originalLanguage={"id": 26, "name": "Hindi"})
        movie = service._parse_movie(data)
        assert movie.original_language == "Hindi"

    def test_parse_movie_without_original_language(self):
        """Missing originalLanguage key results in None."""
        service = self._make_service()
        data = self._base_movie_data()
        movie = service._parse_movie(data)
        assert movie.original_language is None

    def test_parse_movie_with_null_original_language(self):
        """originalLanguage set to None results in None."""
        service = self._make_service()
        data = self._base_movie_data(originalLanguage=None)
        movie = service._parse_movie(data)
        assert movie.original_language is None

    def test_parse_movie_with_non_dict_original_language(self):
        """originalLanguage as a non-dict value results in None."""
        service = self._make_service()
        data = self._base_movie_data(originalLanguage="English")
        movie = service._parse_movie(data)
        assert movie.original_language is None
