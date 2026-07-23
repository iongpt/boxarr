"""Unit tests for the manual box office routes offloading to a worker thread.

The blocking Box Office Mojo scraper is called from async handlers via
``asyncio.to_thread``. These tests patch the service and assert the endpoints
still return the scraped data (and propagate errors) through the threaded path.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.core.boxoffice import BoxOfficeError, BoxOfficeMovie


@pytest.fixture
def client():
    return TestClient(create_app())


class _FakeService:
    """Stands in for BoxOfficeService with synchronous, blocking-style calls."""

    def __init__(self, *_, **__):
        pass

    def get_current_week_movies(self, limit: int = 10):
        return [
            BoxOfficeMovie(
                rank=1,
                title="Threaded Movie",
                weekend_gross=1000.0,
                total_gross=5000.0,
                weeks_released=1,
            )
        ]

    def fetch_weekend_box_office(self, year, week, limit: int = 10):
        return [
            BoxOfficeMovie(
                rank=1,
                title="Historical Movie",
                weekend_gross=2000.0,
                total_gross=8000.0,
                weeks_released=2,
            )
        ]


def test_current_returns_data_via_thread(client):
    """/api/boxoffice/current returns scraped data through the threaded path."""
    with (
        patch("src.api.routes.boxoffice.BoxOfficeService", _FakeService),
        patch("src.api.routes.boxoffice.settings") as mock_settings,
    ):
        mock_settings.radarr_api_key = ""
        resp = client.get("/api/boxoffice/current")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["title"] == "Threaded Movie"
    assert body[0]["weekend_gross"] == 1000.0


def test_history_returns_data_via_thread(client):
    """/api/boxoffice/history returns scraped data through the threaded path."""
    with patch("src.api.routes.boxoffice.BoxOfficeService", _FakeService):
        resp = client.get("/api/boxoffice/history/2024/W48")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["title"] == "Historical Movie"
    assert body[0]["total_gross"] == 8000.0


def test_history_propagates_scraper_error(client):
    """A BoxOfficeError from the threaded scraper still surfaces as HTTP 500."""

    class _FailingService(_FakeService):
        def fetch_weekend_box_office(self, year, week, limit: int = 10):
            raise BoxOfficeError("Box Office Mojo unavailable")

    with patch("src.api.routes.boxoffice.BoxOfficeService", _FailingService):
        resp = client.get("/api/boxoffice/history/2024/W48")

    assert resp.status_code == 500
    assert "Box Office Mojo unavailable" in resp.json()["detail"]
