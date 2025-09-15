import httpx

from src.core.radarr import RadarrService
from src.utils.config import MinimumAvailabilityEnum, settings


class _FakeClient:
    def __init__(self):
        self.last_request = None

    def request(self, method, endpoint, **kwargs):  # type: ignore[override]
        # Simulate Radarr endpoints used by add_movie
        base_url = "http://radarr.local:7878"
        if method == "GET" and endpoint == "/api/v3/movie/lookup":
            req = httpx.Request(method, f"{base_url}{endpoint}")
            return httpx.Response(
                200,
                request=req,
                json=[
                    {
                        "tmdbId": 123,
                        "title": "Test Movie",
                        "titleSlug": "test-movie",
                        "genres": ["Action"],
                        "year": 2025,
                    }
                ],
            )
        if method == "GET" and endpoint == "/api/v3/qualityProfile":
            req = httpx.Request(method, f"{base_url}{endpoint}")
            return httpx.Response(
                200,
                request=req,
                json=[
                    {
                        "id": 1,
                        "name": "HD-1080p",
                        "upgradeAllowed": True,
                        "cutoff": 0,
                        "items": [],
                    }
                ],
            )
        if method == "POST" and endpoint == "/api/v3/movie":
            self.last_request = {"method": method, "endpoint": endpoint, **kwargs}
            # Echo back the added movie
            body = kwargs.get("json", {})
            req = httpx.Request(method, f"{base_url}{endpoint}")
            return httpx.Response(200, request=req, json={"id": 999, **body})

        return httpx.Response(200, json={})

    def close(self):  # pragma: no cover - nothing to close in fake
        pass


def test_add_movie_coerces_unsupported_minimum_availability_to_safe_default(
    monkeypatch,
):
    # Enable minimum availability and set an unsupported legacy value
    settings.radarr_minimum_availability_enabled = True
    settings.radarr_minimum_availability = MinimumAvailabilityEnum.PRE_DB

    fake = _FakeClient()
    service = RadarrService(
        url="http://radarr.local:7878", api_key="test", http_client=fake
    )

    added = service.add_movie(tmdb_id=123, root_folder="/movies")
    assert added.id == 999

    # Ensure payload uses a supported value (announced) when legacy value is configured
    payload = fake.last_request["json"]
    assert payload["minimumAvailability"] == "announced"
