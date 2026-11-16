"""Where a denied page sends the browser: the login page, with ``next``
pointing back (src/api/auth/middleware.py login_url).

A key, token or password sent in a page's URL must not travel on into the
login page's URL, the browser's history and the way back; every other
parameter is kept byte for byte.
"""

from urllib.parse import parse_qs, urlsplit

import pytest

import src.api.auth.middleware as middleware
from src.api.auth.middleware import login_url
from src.utils import logger


def _scope(path: str, query: bytes = b"", root: str = ""):
    return {
        "type": "http",
        "method": "GET",
        "path": root + path,
        "raw_path": (root + path).encode(),
        "root_path": root,
        "query_string": query,
        "headers": [],
    }


def _next(location: str) -> str:
    return parse_qs(urlsplit(location).query, keep_blank_values=True)["next"][0]


@pytest.mark.parametrize(
    "query, kept",
    [
        (b"", "/weeks"),
        (b"page=2&q=a%20b", "/weeks?page=2&q=a%20b"),
        (b"apikey=bxk_fake", "/weeks"),
        (b"page=2&apikey=bxk_fake&q=x", "/weeks?page=2&q=x"),
        (b"API_KEY=fake&page=2", "/weeks?page=2"),
        (b"Api%4Bey=fake", "/weeks"),  # ApiKey, percent-encoded
        (b"api%5Fkey=fake&page=2", "/weeks?page=2"),
        (b"%61pikey=fake", "/weeks"),
        (b"token=bxw_fake&password=fake-pass", "/weeks"),
        (b"+apikey+=fake", "/weeks"),
        (b"apikey", "/weeks"),
        (b"apikeys=1", "/weeks?apikeys=1"),
    ],
)
def test_next_never_carries_a_secret(query, kept):
    location = login_url(_scope("/weeks", query))

    assert location.startswith("/login?next=")
    assert _next(location) == kept
    assert "fake" not in location or "fake" in kept


def test_next_keeps_the_url_base():
    location = login_url(_scope("/overview", b"apikey=bxk_fake&page=3", "/boxarr"))

    assert location == "/boxarr/login?next=/overview%3Fpage%3D3"


def test_the_dropped_names_are_the_ones_the_access_log_redacts():
    assert middleware._NEXT_DROPPED_NAMES == logger._SECRET_QUERY_NAMES
