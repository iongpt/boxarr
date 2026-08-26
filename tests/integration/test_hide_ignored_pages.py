"""Integration tests: the "hide ignored movies" setting on the rendered pages.

Hiding has to stay coherent: the weekly header must not claim more movies than
the grid shows, the cards must stay in the DOM so the reveal toggle can bring
them back without a round-trip, and the Overview "Ignored" tab must keep
listing them - it is the only guaranteed way to un-ignore.
"""

import json
from datetime import datetime
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
        "weekend_gross": 3000000,
        "year": 2024,
    },
    {
        "rank": 2,
        "title": "Ignored Movie",
        "tmdb_id": 2,
        "radarr_id": None,
        "has_file": False,
        "status": "Not in Radarr",
        "weekend_gross": 2000000,
        "year": 2024,
    },
    {
        "rank": 3,
        "title": "Missing Movie",
        "tmdb_id": 3,
        "radarr_id": 33,
        "has_file": False,
        "status": "Missing",
        "weekend_gross": 1000000,
        "year": 2024,
    },
]


def _seed(
    tmp_path: Path, hide_ignored: bool, ignored: bool = True, movies=None
) -> None:
    weekly_pages = tmp_path / "weekly_pages"
    weekly_pages.mkdir(parents=True, exist_ok=True)
    (weekly_pages / "2024W10.json").write_text(
        json.dumps(
            {
                "year": 2024,
                "week": 10,
                "movies": WEEK_MOVIES if movies is None else movies,
            }
        )
    )

    if ignored:
        (tmp_path / "ignored_movies.json").write_text(
            json.dumps(
                [
                    {
                        "tmdb_id": 2,
                        "title": "Ignored Movie",
                        "ignored_at": datetime.now().isoformat(),
                    }
                ]
            )
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
                    "ui": {"theme": "light", "hide_ignored": hide_ignored},
                },
            }
        )
    )


def _client(tmp_path: Path, monkeypatch, **seed) -> TestClient:
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    _seed(tmp_path, **seed)
    # monkeypatch, not Settings.reload_from_file: the latter would leak a
    # Settings built from this (soon deleted) tmp_path into later tests.
    monkeypatch.setattr(cfg_utils, "_settings", None)
    return TestClient(create_app())


def _page(tmp_path, monkeypatch, url: str, **seed) -> BeautifulSoup:
    response = _client(tmp_path, monkeypatch, **seed).get(url)
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def _card(soup: BeautifulSoup, tmdb_id: int):
    return soup.find("div", attrs={"class": "movie-card", "data-tmdb-id": str(tmdb_id)})


def _button_is_shown(card, css_class: str) -> bool:
    button = card.find("button", class_=css_class)
    assert button is not None, f"no {css_class} on the card"
    return "display:none" not in (button.get("style") or "").replace(" ", "")


def _summary(soup: BeautifulSoup) -> dict:
    return {
        key: soup.find(id=element_id).get_text(strip=True)
        for key, element_id in (
            ("total", "summaryTotal"),
            ("in_radarr", "summaryInRadarr"),
            ("downloaded", "summaryDownloaded"),
            ("missing", "summaryMissing"),
        )
    }


class TestWeeklyPage:
    def test_ignored_card_is_only_dimmed_when_the_setting_is_off(
        self, tmp_path, monkeypatch
    ) -> None:
        soup = _page(tmp_path, monkeypatch, "/2024W10", hide_ignored=False)

        card = _card(soup, 2)
        assert "ignored" in card["class"]
        assert "ignored-hidden" not in card["class"]
        assert _summary(soup) == {
            "total": "3",
            "in_radarr": "2",
            "downloaded": "1",
            "missing": "1",
        }
        assert soup.find(id="revealIgnoredBtn") is None

    def test_ignored_card_is_hidden_but_still_rendered(
        self, tmp_path, monkeypatch
    ) -> None:
        """The card stays in the DOM so the reveal toggle needs no round-trip."""
        soup = _page(tmp_path, monkeypatch, "/2024W10", hide_ignored=True)

        card = _card(soup, 2)
        assert card is not None
        assert "ignored-hidden" in card["class"]
        # The movies that are not ignored keep rendering untouched.
        assert "ignored-hidden" not in _card(soup, 1)["class"]
        # Revealing the card is only useful if it still offers the way out.
        assert _button_is_shown(card, "unignore-btn")
        assert not _button_is_shown(card, "ignore-btn")
        assert _button_is_shown(_card(soup, 1), "ignore-btn")

    def test_header_counts_follow_the_cards_on_screen(
        self, tmp_path, monkeypatch
    ) -> None:
        """The client recount keys off visibility, not off ignore state.

        Filtering on ``.ignored`` instead would recompute "2 Total Movies" over
        the 3 cards the reveal toggle just put back on screen.
        """
        soup = _page(tmp_path, monkeypatch, "/2024W10", hide_ignored=True)

        assert "!card.classList.contains('ignored-hidden')" in soup.decode()

    def test_header_counts_exclude_hidden_movies(self, tmp_path, monkeypatch) -> None:
        """Otherwise the page claims 3 movies above 2 visible cards."""
        soup = _page(tmp_path, monkeypatch, "/2024W10", hide_ignored=True)

        assert _summary(soup) == {
            "total": "2",
            "in_radarr": "2",
            "downloaded": "1",
            "missing": "0",
        }

    def test_reveal_toggle_reports_the_hidden_count(
        self, tmp_path, monkeypatch
    ) -> None:
        soup = _page(tmp_path, monkeypatch, "/2024W10", hide_ignored=True)

        button = soup.find(id="revealIgnoredBtn")
        assert button is not None
        assert "1 ignored" in button.get_text(strip=True)
        assert "display:none" not in (button.get("style") or "")

    def test_reveal_toggle_is_not_displayed_without_ignored_movies(
        self, tmp_path, monkeypatch
    ) -> None:
        """It stays in the DOM so ignoring a movie can surface it live."""
        soup = _page(
            tmp_path, monkeypatch, "/2024W10", hide_ignored=True, ignored=False
        )

        button = soup.find(id="revealIgnoredBtn")
        assert button is not None
        assert "display:none" in button["style"].replace(" ", "")


class TestOverviewPage:
    def test_ignored_movie_is_listed_when_the_setting_is_off(
        self, tmp_path, monkeypatch
    ) -> None:
        soup = _page(tmp_path, monkeypatch, "/overview", hide_ignored=False)

        assert _card(soup, 2) is not None
        assert "ignored" in _card(soup, 2)["class"]

    def test_ignored_movie_is_absent_from_the_all_view(
        self, tmp_path, monkeypatch
    ) -> None:
        soup = _page(tmp_path, monkeypatch, "/overview", hide_ignored=True)

        assert _card(soup, 2) is None
        assert _card(soup, 1) is not None
        # "Total Movies" counts what the grid shows, like every other bucket.
        assert soup.find_all("span", class_="stat-value")[0].get_text(strip=True) == "2"

    def test_ignored_movie_is_absent_from_a_status_view(
        self, tmp_path, monkeypatch
    ) -> None:
        soup = _page(
            tmp_path, monkeypatch, "/overview?status=not_in_radarr", hide_ignored=True
        )

        assert _card(soup, 2) is None

    def test_ignored_tab_still_lists_ignored_movies(
        self, tmp_path, monkeypatch
    ) -> None:
        """The escape hatch: hiding must never lock a movie out of reach."""
        soup = _page(
            tmp_path, monkeypatch, "/overview?status=ignored", hide_ignored=True
        )

        card = _card(soup, 2)
        assert card is not None
        assert "ignored-hidden" not in card["class"]
        # ...and the client-side hiding stays off there, so a card cannot
        # vanish from the very list that exists to show it.
        assert "window.HIDE_IGNORED = false" in soup.decode()
        # The un-ignore button is the escape hatch itself: it has to be the
        # visible one on an ignored card.
        assert _button_is_shown(card, "unignore-btn")
        assert not _button_is_shown(card, "ignore-btn")

    def test_counts_can_follow_a_card_hidden_client_side(
        self, tmp_path, monkeypatch
    ) -> None:
        """Ignoring here drops the card, so the numbers around it must move.

        They are server-rendered, so the page exposes them by id and re-derives
        them from the cards it has hidden since load.
        """
        soup = _page(tmp_path, monkeypatch, "/overview", hide_ignored=True)

        assert soup.find(id="showingCount").get_text(strip=True) == "2"
        assert soup.find(id="totalMovieCount").get_text(strip=True) == "2"
        assert soup.find(id="statTotal").get_text(strip=True) == "2"
        assert soup.find(id="statIgnored").get_text(strip=True) == "1"
        assert "window.onIgnoredVisibilityChange = function" in soup.decode()

    def test_every_stat_card_is_reachable_by_id(self, tmp_path, monkeypatch) -> None:
        """Ignoring a card moves Total, so the buckets have to move with it.

        Only Total and Ignored used to carry an id, so after one ignore the
        row claimed In Radarr + Not in Radarr more than Total - and the four
        stale buckets jumped to their real values on the next reload.
        """
        soup = _page(tmp_path, monkeypatch, "/overview", hide_ignored=True)

        assert soup.find(id="statInRadarr").get_text(strip=True) == "2"
        assert soup.find(id="statDownloaded").get_text(strip=True) == "1"
        assert soup.find(id="statMissing").get_text(strip=True) == "1"
        assert soup.find(id="statNotInRadarr").get_text(strip=True) == "0"
        assert all(
            span.has_attr("id") for span in soup.find_all("span", class_="stat-value")
        )

    def test_recount_covers_every_counter_on_the_row(
        self, tmp_path, monkeypatch
    ) -> None:
        """The hidden card carries its own buckets: a Radarr id and a bucket.

        Pinned to the expression that moves each counter rather than to the
        counter's name: six of the eight names also appear in the baseline
        object the hook subtracts from, so a name-only assertion stays green
        with the update for that counter deleted.
        """
        page = _page(tmp_path, monkeypatch, "/overview", hide_ignored=True).decode()

        for update in (
            "showingCount: Math.max(0, rendered.showing - hidden)",
            "totalMovieCount: Math.max(0, rendered.total - hidden)",
            "statTotal: Math.max(0, rendered.statTotal - hidden)",
            "statInRadarr: Math.max(0, rendered.statInRadarr - delta.inRadarr)",
            "statDownloaded: Math.max(0, rendered.statDownloaded - delta.downloaded)",
            "statMissing: Math.max(0, rendered.statMissing - delta.missing)",
            "statNotInRadarr: Math.max(0, rendered.statNotInRadarr - delta.notInRadarr)",
            "statIgnored: rendered.statIgnored + hidden",
        ):
            assert update in page, update
        assert "card.dataset.movieId" in page
        assert "card.dataset.statBucket === 'downloaded'" in page
        assert "card.dataset.statBucket === 'missing'" in page

    def test_each_card_carries_the_bucket_it_was_counted_in(
        self, tmp_path, monkeypatch
    ) -> None:
        """The status badge is not that bucket, so it cannot stand in for it.

        The badge is painted from has_file, then "In Cinemas", then everything
        else - so a "Pending" movie (announced, or released but not yet
        available) wears the Missing badge while the server counted it in no
        bucket at all. updateMovieStatuses() also repaints every badge from
        live Radarr after load, while the counters stay on the rendered
        snapshot. Either way a badge-derived delta takes Missing below what the
        grid is still showing, and it snaps back on the next reload.
        """
        pending = dict(
            WEEK_MOVIES[2],
            rank=4,
            title="Pending Movie",
            tmdb_id=4,
            radarr_id=44,
            status="Pending",
        )
        soup = _page(
            tmp_path,
            monkeypatch,
            "/overview",
            hide_ignored=True,
            movies=WEEK_MOVIES + [pending],
        )

        assert soup.find(id="statMissing").get_text(strip=True) == "1"
        assert _card(soup, 1)["data-stat-bucket"] == "downloaded"
        assert _card(soup, 3)["data-stat-bucket"] == "missing"
        assert "missing" in _card(soup, 4).find(class_="status-badge")["class"]
        assert _card(soup, 4)["data-stat-bucket"] == ""

    def test_ignored_tab_reports_the_real_total(self, tmp_path, monkeypatch) -> None:
        """Nothing is hidden on the escape hatch, so nothing may be deducted."""
        soup = _page(
            tmp_path, monkeypatch, "/overview?status=ignored", hide_ignored=True
        )

        assert soup.find(id="statTotal").get_text(strip=True) == "3"

    def test_ignored_stat_card_keeps_the_real_count(
        self, tmp_path, monkeypatch
    ) -> None:
        soup = _page(tmp_path, monkeypatch, "/overview", hide_ignored=True)

        assert (
            soup.find_all("span", class_="stat-value")[-1].get_text(strip=True) == "1"
        )


class TestSetupPage:
    def test_checkbox_reflects_the_stored_setting(self, tmp_path, monkeypatch) -> None:
        on = _page(tmp_path, monkeypatch, "/setup", hide_ignored=True)
        assert on.find(id="hideIgnored").has_attr("checked")

    def test_checkbox_is_unticked_by_default(self, tmp_path, monkeypatch) -> None:
        off = _page(tmp_path, monkeypatch, "/setup", hide_ignored=False)
        assert not off.find(id="hideIgnored").has_attr("checked")

    def test_checkbox_is_read_explicitly_not_through_formdata(
        self, tmp_path, monkeypatch
    ) -> None:
        """No name attribute: the explicit JS read is the only writer.

        An unchecked checkbox is absent from FormData, and the server carries an
        omitted flag over - so the save path has to post an explicit false.
        """
        soup = _page(tmp_path, monkeypatch, "/setup", hide_ignored=True)
        assert not soup.find(id="hideIgnored").has_attr("name")

        app_js = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "web"
            / "static"
            / "js"
            / "app.js"
        ).read_text()
        assert (
            "config.boxarr_ui_hide_ignored = "
            "document.getElementById('hideIgnored')?.checked || false;" in app_js
        )

    def test_help_text_points_at_the_un_ignore_escape_hatch(
        self, tmp_path, monkeypatch
    ) -> None:
        soup = _page(tmp_path, monkeypatch, "/setup", hide_ignored=True)
        section = soup.find(id="hideIgnored").find_parent(
            "div", class_="checkbox-group"
        )

        assert "Ignored" in section.get_text()
        assert "Overview" in section.get_text()
