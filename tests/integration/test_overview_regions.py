"""Integration tests: the overview, week list and widget over merged weeks.

A week built from several box office regions holds one entry per film, with
a row per region. The overview lists each film once with every week it
appeared in - once per week - its best rank, and as "Best $" the weekend
gross of the week it reached that rank (the first such week on a tie), as in
v2.1.2. Only inside that week is the gross the highest across its regions. A
stored null rank or gross (both are written for films Mojo reported without
one) must never 500 the page.

GET /api/weeks and the overview's recent weeks keep their shape; a week's
movie_count is its number of films. The widget JSON names each film's region.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import src.utils.config as cfg_utils
from src.api.app import create_app
from src.api.routes.web import aggregate_all_movies


def _movie(
    title: str,
    tmdb_id: Optional[int],
    rank: Any,
    gross: Any,
    region: Optional[str] = None,
    rows: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    movie: Dict[str, Any] = {
        "rank": rank,
        "title": title,
        "weekend_gross": gross,
        "total_gross": None,
        "radarr_id": None,
        "status": "Not in Radarr",
        "has_file": False,
        "year": 2026,
        "tmdb_id": tmdb_id,
    }
    if region is not None:
        movie["region"] = region
        movie["regions"] = rows or [
            {"code": region, "rank": rank, "weekend_gross": gross}
        ]
    return movie


def _week(week: int, movies: List[dict], regions: Optional[List[str]] = None):
    data: Dict[str, Any] = {
        "year": 2026,
        "week": week,
        "generated_at": "2026-11-03T10:00:00",
        "movies": movies,
    }
    if regions is not None:
        data.update(
            {
                "data_version": 2,
                "regions": regions,
                "region_status": [{"region": code, "status": "ok"} for code in regions],
            }
        )
    return data


@pytest.fixture
def seed(tmp_path, monkeypatch):
    for field in cfg_utils.Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
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

    def _seed(*weeks: dict) -> TestClient:
        pages = tmp_path / "weekly_pages"
        pages.mkdir(parents=True, exist_ok=True)
        for data in weeks:
            name = f"{data['year']}W{data['week']:02d}.json"
            (pages / name).write_text(json.dumps(data))
        monkeypatch.setattr(cfg_utils, "_settings", None)
        return TestClient(create_app())

    return _seed


def _aggregate() -> Dict[str, dict]:
    return {m["title"]: m for m in asyncio.run(aggregate_all_movies())}


class TestOverview:
    def test_null_gross_and_rank_do_not_break_the_page(self, seed):
        client = seed(
            _week(
                44,
                [
                    _movie("Has Gross", 1, 1, 5_000_000.0),
                    _movie("No Gross", 2, None, None),
                    _movie("String Gross", 3, "x", "n/a"),
                ],
            )
        )

        response = client.get("/overview")

        assert response.status_code == 200
        soup = BeautifulSoup(response.text, "html.parser")
        titles = [h.get_text(strip=True) for h in soup.find_all(class_="movie-title")]
        assert titles == ["Has Gross", "No Gross", "String Gross"]
        badges = [b.get_text(strip=True) for b in soup.find_all(class_="rank-badge")]
        assert badges == ["#1", "#999", "#999"]

    def test_null_gross_in_a_merged_week_row(self, seed):
        client = seed(
            _week(
                44,
                [
                    _movie(
                        "Clayface",
                        1,
                        1,
                        None,
                        region="",
                        rows=[
                            {"code": "", "rank": 1, "weekend_gross": None},
                            {"code": "RO", "rank": 3, "weekend_gross": 60_800.0},
                        ],
                    ),
                    _movie("Other", 2, 2, None, region="RO"),
                ],
                regions=["", "RO"],
            )
        )

        assert client.get("/overview").status_code == 200
        movies = _aggregate()
        assert movies["Clayface"]["best_weekend_gross"] == 60_800.0
        assert movies["Other"]["best_weekend_gross"] == 0

    def test_a_film_counts_each_week_once(self, seed):
        # Two entries for one TMDB film in one week: e.g. both already linked
        # to Radarr, which the merge leaves apart.
        seed(
            _week(
                44,
                [
                    _movie("Clayface", 7, 1, 11_000_000.0, region=""),
                    _movie("Clayface (RO)", 7, 3, 60_800.0, region="RO"),
                ],
                regions=["", "RO"],
            ),
            _week(45, [_movie("Clayface", 7, 2, 4_000_000.0, region="")], [""]),
        )

        (movie,) = asyncio.run(aggregate_all_movies())

        assert movie["weeks"] == ["2026W44", "2026W45"]
        assert movie["best_rank"] == 1
        assert movie["best_weekend_gross"] == 11_000_000.0

    def test_best_gross_is_the_gross_of_the_best_rank_week(self, seed):
        # Opened at #2 with more money, then reached #1 in a slower week.
        seed(
            _week(44, [_movie("Film", 1, 2, 150_000_000.0)]),
            _week(45, [_movie("Film", 1, 1, 90_022_510.0)]),
        )

        movie = _aggregate()["Film"]

        assert movie["best_rank"] == 1
        assert movie["best_weekend_gross"] == 90_022_510.0
        assert movie["weeks"] == ["2026W44", "2026W45"]

    def test_the_best_rank_week_counts_its_best_region(self, seed):
        seed(
            # Week 43: #3 in a busy week.
            _week(43, [_movie("Film", 1, 3, 3_000_000.0)]),
            # Week 44: led the Romanian chart, and grossed more at #4 domestic.
            _week(
                44,
                [
                    _movie(
                        "Film",
                        1,
                        1,
                        200_000.0,
                        region="RO",
                        rows=[
                            {"code": "", "rank": 4, "weekend_gross": 5_000_000.0},
                            {"code": "RO", "rank": 1, "weekend_gross": 200_000.0},
                        ],
                    )
                ],
                regions=["", "RO"],
            ),
        )

        movie = _aggregate()["Film"]

        assert movie["best_rank"] == 1
        assert movie["best_weekend_gross"] == 5_000_000.0
        assert movie["weeks"] == ["2026W43", "2026W44"]

    def test_a_region_gross_of_another_week_is_never_used(self, seed):
        seed(
            # Week 43: #1 in a slow week.
            _week(43, [_movie("Film", 1, 1, 3_000_000.0)]),
            # Week 44: #1 again (a tie: the first week stays), $5M domestic.
            _week(
                44,
                [
                    _movie(
                        "Film",
                        1,
                        1,
                        200_000.0,
                        region="RO",
                        rows=[
                            {"code": "", "rank": 4, "weekend_gross": 5_000_000.0},
                            {"code": "RO", "rank": 1, "weekend_gross": 200_000.0},
                        ],
                    )
                ],
                regions=["", "RO"],
            ),
            # Week 45: #2 with the most money of all.
            _week(45, [_movie("Film", 1, 2, 9_000_000.0, region="")], [""]),
        )

        movie = _aggregate()["Film"]

        assert movie["best_rank"] == 1
        assert movie["best_weekend_gross"] == 3_000_000.0

    def test_a_missing_rank_counts_as_999(self, seed):
        seed(
            _week(43, [_movie("Unranked", 1, None, 7_000.0)]),
            _week(44, [_movie("Unranked", 1, None, 9_000.0)]),
            _week(45, [_movie("Ranked Later", 2, None, 8_000.0)]),
            _week(46, [_movie("Ranked Later", 2, 5, 1_000.0)]),
        )

        movies = _aggregate()

        assert movies["Unranked"]["best_rank"] == 999
        assert movies["Unranked"]["best_weekend_gross"] == 7_000.0
        assert movies["Ranked Later"]["best_rank"] == 5
        assert movies["Ranked Later"]["best_weekend_gross"] == 1_000.0

    def test_a_null_gross_in_the_best_rank_week_is_not_replaced(self, seed):
        client = seed(
            _week(43, [_movie("Film", 1, 2, 4_000_000.0)]),
            _week(44, [_movie("Film", 1, 1, None)]),
        )

        assert client.get("/overview").status_code == 200
        movie = _aggregate()["Film"]
        assert movie["best_rank"] == 1
        assert movie["best_weekend_gross"] == 0

    def test_films_are_sorted_by_best_gross_nulls_last(self, seed):
        seed(
            _week(
                44,
                [
                    _movie("Small", 1, 3, 10.0),
                    _movie("Null", 2, 2, None),
                    _movie("Big", 3, 1, 1_000.0),
                ],
            )
        )

        titles = [m["title"] for m in asyncio.run(aggregate_all_movies())]

        assert titles == ["Big", "Small", "Null"]

    def test_search_survives_a_null_title(self, seed):
        client = seed(
            _week(44, [_movie(None, 1, 1, 10.0), _movie("Wicker", 2, 2, 5.0)])
        )

        response = client.get("/overview?search=wick")

        assert response.status_code == 200
        assert "Wicker" in response.text


class TestOverviewRankBadge:
    """The badge of a best rank from a week of several regions names its region."""

    def _badges(self, client) -> Dict[str, str]:
        soup = BeautifulSoup(client.get("/overview").text, "html.parser")
        return {
            card.find(class_="movie-title")
            .get_text(strip=True): card.find(class_="rank-badge")
            .get_text(" ", strip=True)
            for card in soup.find_all(class_="movie-card")
        }

    def test_the_rank_names_the_region_it_is_from(self, seed):
        client = seed(
            _week(
                44,
                [
                    _movie("Dom", 1, 1, 9_000_000.0, region=""),
                    _movie(
                        "Both",
                        2,
                        2,
                        500_000.0,
                        region="FR",
                        rows=[
                            {"code": "", "rank": 7, "weekend_gross": 1_000_000.0},
                            {"code": "FR", "rank": 2, "weekend_gross": 500_000.0},
                        ],
                    ),
                ],
                regions=["", "FR"],
            )
        )

        assert self._badges(client) == {"Dom": "#1 DOM", "Both": "#2 FR"}

    def test_the_tag_follows_the_week_of_the_best_rank(self, seed):
        client = seed(
            _week(44, [_movie("Film", 1, 3, 1_000.0, region="FR")], ["", "FR"]),
            _week(45, [_movie("Film", 1, 1, 2_000.0, region="FR")], ["FR"]),
            _week(46, [_movie("Other", 2, 1, 3_000.0, region="FR")], ["FR"]),
            _week(47, [_movie("Other", 2, 4, 4_000.0, region="DE")], ["DE", "FR"]),
        )

        # The best rank of each came from a week of one region: no tag.
        assert self._badges(client) == {"Film": "#1", "Other": "#1"}

    def test_a_page_of_one_region_weeks_is_unchanged(self, seed):
        client = seed(_week(44, [_movie("Film", 1, 1, 1_000.0, region="FR")], ["FR"]))

        soup = BeautifulSoup(client.get("/overview").text, "html.parser")

        assert str(soup.find(class_="rank-badge")) == '<div class="rank-badge">#1</div>'


class TestWeekList:
    def test_shape_is_unchanged_and_counts_films(self, seed):
        client = seed(
            _week(
                44,
                [
                    _movie("A", 1, 1, 10.0, region=""),
                    _movie("B", 2, 1, 5.0, region="RO"),
                    _movie("C", 3, 2, 4.0, region=""),
                ],
                regions=["", "RO"],
            )
        )

        (week,) = client.get("/api/weeks").json()

        assert set(week) == {
            "year",
            "week",
            "filename",
            "date_range",
            "movie_count",
            "matched_count",
            "has_data",
            "timestamp_str",
        }
        assert week["movie_count"] == 3


class TestWidget:
    def test_widget_json_names_each_films_region(self, seed):
        client = seed(
            _week(
                44,
                [
                    _movie("A", 1, 1, 10.0, region=""),
                    _movie("B", 2, 1, 5.0, region="RO"),
                ],
                regions=["", "RO"],
            )
        )

        movies = client.get("/api/widget/json").json()["movies"]

        assert movies == [
            {"rank": 1, "title": "A", "gross": 10.0, "region": "", "region_tag": "DOM"},
            {"rank": 1, "title": "B", "gross": 5.0, "region": "RO", "region_tag": "RO"},
        ]

    def test_widget_html_tags_each_film_with_its_region(self, seed):
        client = seed(
            _week(
                44,
                [
                    _movie("A", 1, 1, 10.0, region=""),
                    _movie("B", 2, 1, 5.0, region="RO"),
                ],
                regions=["", "RO"],
            )
        )

        soup = BeautifulSoup(client.get("/api/widget").text, "html.parser")

        items = [li.get_text(" ", strip=True) for li in soup.find_all("li")]
        assert items == ["A DOM", "B RO"]

    def test_a_hand_edited_region_is_escaped_in_the_widget_html(self, seed):
        client = seed(
            _week(
                44,
                [
                    _movie("A", 1, 1, 10.0, region=""),
                    _movie("B", 2, 1, 5.0, region="<img src=x onerror=alert(1)>"),
                ],
                regions=["", "<img src=x onerror=alert(1)>"],
            )
        )

        widget = client.get("/api/widget").text

        assert "<img" not in widget
        assert "&lt;img src=x onerror=alert(1)&gt;" in widget

    def test_a_week_of_one_region_is_unchanged(self, seed):
        client = seed(_week(44, [_movie("A", 1, 1, 10.0, region="FR")], ["FR"]))

        movies = client.get("/api/widget/json").json()["movies"]
        soup = BeautifulSoup(client.get("/api/widget").text, "html.parser")

        assert movies == [{"rank": 1, "title": "A", "gross": 10.0, "region": "FR"}]
        assert str(soup.find("li")) == "<li>A</li>"

    def test_legacy_week_has_no_region(self, seed):
        client = seed(_week(44, [_movie("A", 1, 1, 10.0)]))

        movies = client.get("/api/widget/json").json()["movies"]

        assert movies == [{"rank": 1, "title": "A", "gross": 10.0, "region": None}]
