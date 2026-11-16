"""Integration tests: the page footer shows the current year.

The footer said "(c) 2026 Boxarr" as a literal, so every page would carry a
stale year from 1 January 2027. It is now read from the server clock on each
request. The clock is pinned to years other than 2026 so the tests cannot pass
because the literal happens to match today.
"""

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.api.routes.web as web
import src.utils.config as cfg_utils
from src.api.app import create_app

# Every page that extends base.html. setup.html has no footer.
PAGES = ("/overview", "/dashboard", "/2024W10")


def _seed(tmp_path: Path) -> None:
    weekly_pages = tmp_path / "weekly_pages"
    weekly_pages.mkdir(parents=True, exist_ok=True)
    (weekly_pages / "2024W10.json").write_text(
        json.dumps({"year": 2024, "week": 10, "movies": []})
    )
    (tmp_path / "local.yaml").write_text(
        yaml.safe_dump(
            {
                "radarr": {
                    "url": "http://localhost:7878",
                    "api_key": "test-key",
                    "root_folder": "/movies",
                    "quality_profile_default": "HD-1080p",
                },
                "boxarr": {
                    "scheduler": {"enabled": False, "cron": "0 23 * * 2"},
                    "ui": {"theme": "light"},
                },
            }
        )
    )


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed(tmp_path)
    # monkeypatch, not Settings.reload_from_file: the latter would leak a
    # Settings built from this (soon deleted) tmp_path into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


def _footer_text(client: TestClient, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    footer = BeautifulSoup(response.text, "html.parser").select_one("footer.footer")
    assert footer is not None, f"{url} has no footer"
    return footer.get_text(" ", strip=True)


@pytest.mark.parametrize("url", PAGES)
def test_footer_shows_the_current_year(tmp_path, monkeypatch, url) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "_today", lambda: date(2031, 3, 4))

    text = _footer_text(client, url)

    assert "© 2031 Boxarr" in text
    assert "2026" not in text


def test_footer_year_rolls_over_without_a_restart(tmp_path, monkeypatch) -> None:
    """The year is read per request, not once when the app starts."""
    client = _client(tmp_path, monkeypatch)

    monkeypatch.setattr(web, "_today", lambda: date(2027, 12, 31))
    assert "© 2027 Boxarr" in _footer_text(client, "/overview")

    monkeypatch.setattr(web, "_today", lambda: date(2028, 1, 1))
    assert "© 2028 Boxarr" in _footer_text(client, "/overview")
