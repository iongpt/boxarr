"""The weekly header labels its not-in-Radarr count as such (issue #149).

The fourth header counter counts movies that are not in Radarr, but it was
labelled "Missing" - the word the "⬇ Missing" badges on the same page use for
movies that ARE in Radarr without a file. Only the label changes: the element
id and the summary key stay, because the client recount and tests use them.
"""

import json
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.utils.config as cfg_utils
from src.api.app import create_app

WEEK_MOVIES = [
    {
        "rank": 1,
        "title": "Downloaded Movie",
        "tmdb_id": 1,
        "radarr_id": 11,
        "has_file": True,
        "status": "Downloaded",
        "year": 2024,
    },
    {
        "rank": 2,
        "title": "Missing Movie",
        "tmdb_id": 2,
        "radarr_id": 22,
        "has_file": False,
        "status": "Missing",
        "year": 2024,
    },
    {
        "rank": 3,
        "title": "Absent Movie",
        "tmdb_id": 3,
        "radarr_id": None,
        "has_file": False,
        "status": "Not in Radarr",
        "year": 2024,
    },
]


def _weekly_page(tmp_path: Path, monkeypatch) -> BeautifulSoup:
    weekly_pages = tmp_path / "weekly_pages"
    weekly_pages.mkdir(parents=True)
    (weekly_pages / "2024W10.json").write_text(
        json.dumps({"year": 2024, "week": 10, "movies": WEEK_MOVIES})
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
                "boxarr": {"scheduler": {"enabled": False}},
            }
        )
    )
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(cfg_utils, "_settings", None)

    response = TestClient(create_app()).get("/2024W10")
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def _summary_labels(soup: BeautifulSoup) -> dict:
    """Map each header label to its rendered value."""
    labels = {}
    for item in soup.select(".summary .summary-item"):
        label = item.find(class_="summary-label").get_text(strip=True)
        labels[label] = item.find(class_="summary-value").get_text(strip=True)
    return labels


def test_not_in_radarr_counter_is_labelled_not_in_radarr(tmp_path, monkeypatch):
    soup = _weekly_page(tmp_path, monkeypatch)

    item = soup.find(id="summaryMissing").find_parent(class_="summary-item")
    assert item.find(class_="summary-label").get_text(strip=True) == "Not in Radarr"
    assert soup.find(id="summaryMissing").get_text(strip=True) == "1"


def test_header_no_longer_says_missing(tmp_path, monkeypatch):
    """The word Missing stays with the badge for in-Radarr movies without a file."""
    soup = _weekly_page(tmp_path, monkeypatch)

    assert _summary_labels(soup) == {
        "Total Movies": "3",
        "In Radarr": "2",
        "Downloaded": "1",
        "Not in Radarr": "1",
    }
    # The header now agrees with the badges: one "Not in Radarr" card, and the
    # in-Radarr movie without a file keeps its "Missing" badge.
    assert len(soup.select(".movie-card .status-badge.not-in-radarr")) == 1
    assert len(soup.select(".movie-card .status-badge.missing")) == 1
