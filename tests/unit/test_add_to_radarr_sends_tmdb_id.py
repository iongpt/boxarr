"""The "Add to Radarr" button must post the card's TMDB id when it has one.

Without it the server adds the first TMDB hit for the Box Office Mojo title,
which can be a different film than the card shows, and the stored week entry
(which is relinked by TMDB id after the add) would then never turn "In Radarr".
The button passes only ``(title, year, this)``, so ``addToRadarr`` reads the id
from the ``data-tmdb-id`` attribute of the enclosing ``.movie-card``.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "src" / "web"
APP_JS = ROOT / "static" / "js" / "app.js"
TEMPLATES = ROOT / "templates"


def _add_to_radarr_source() -> str:
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("window.addToRadarr = function")
    end = source.index("window.upgradeQuality = function", start)
    return source[start:end]


def test_add_to_radarr_reads_tmdb_id_from_the_enclosing_card():
    body = _add_to_radarr_source()

    assert "closest('.movie-card')" in body
    assert "dataset.tmdbId" in body
    # Only a real positive id is sent: an empty data-tmdb-id="" would otherwise
    # post an invalid tmdb_id and fail the add with a 422.
    assert re.search(r"payload\.tmdb_id\s*=\s*tmdbId", body)
    assert "tmdbId > 0" in body


def test_add_to_radarr_posts_the_payload_with_tmdb_id():
    body = _add_to_radarr_source()

    assert "apiUrl('/movies/add')" in body
    assert "body: JSON.stringify(payload)" in body
    assert "JSON.stringify({ title, year })" not in body


@pytest.mark.parametrize("template_name", ["weekly.html", "overview.html"])
def test_add_button_sits_inside_a_card_carrying_its_tmdb_id(template_name):
    content = (TEMPLATES / template_name).read_text(encoding="utf-8")

    buttons = [m.start() for m in re.finditer(r"addToRadarr\(", content)]
    assert buttons, f"{template_name} has no Add to Radarr button"
    for position in buttons:
        card_start = content.rfind('<div class="movie-card', 0, position)
        assert card_start != -1, "Add to Radarr button outside a .movie-card"
        card_tag = content[card_start : content.index(">", card_start)]
        assert "data-tmdb-id=\"{{ movie.tmdb_id or '' }}\"" in card_tag
