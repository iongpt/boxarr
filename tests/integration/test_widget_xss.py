"""Integration tests for stored XSS hardening in the /api/widget endpoint.

Scraped Box Office Mojo titles are attacker-influenced input persisted into
weekly JSON. The widget builds HTML by hand (not via autoescaped Jinja), so
every dynamic value must be HTML-escaped before it reaches the browser.
"""

import json

from fastapi.testclient import TestClient

from src.api.app import create_app


def _write_week(data_dir, title):
    """Create a minimal weekly JSON fixture with a single movie title."""
    weekly_pages = data_dir / "weekly_pages"
    weekly_pages.mkdir(parents=True, exist_ok=True)
    (weekly_pages / "2026W30.json").write_text(
        json.dumps(
            {
                "year": 2026,
                "week": 30,
                "movies": [
                    {"rank": 1, "title": title, "weekend_gross": 1000000},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_widget_escapes_stored_title(monkeypatch, tmp_path):
    """A stored title with HTML must be escaped in the widget response."""
    monkeypatch.setattr(
        "src.api.routes.web.settings.boxarr_data_directory", str(tmp_path)
    )
    _write_week(tmp_path, "<script>alert(1)</script>")

    client = TestClient(create_app())
    response = client.get("/api/widget")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_widget_escapes_title_attribute_breakers(monkeypatch, tmp_path):
    """Quote/angle characters that could break out of markup are escaped."""
    monkeypatch.setattr(
        "src.api.routes.web.settings.boxarr_data_directory", str(tmp_path)
    )
    _write_week(tmp_path, '"><img src=x onerror=alert(1)>')

    client = TestClient(create_app())
    response = client.get("/api/widget")

    assert response.status_code == 200
    assert "<img src=x onerror=alert(1)>" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
