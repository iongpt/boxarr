"""The login ``next`` validator (#151, design §3.3.5, MF2).

The vectors live in tests/fixtures/auth/redirect_vectors.json so the login
page's JavaScript validator can run the same ones.
"""

import json
from pathlib import Path

import pytest

from src.api.auth.redirects import FALLBACK, MAX_LENGTH, validate_next

VECTOR_FILE = Path(__file__).parents[1] / "fixtures" / "auth" / "redirect_vectors.json"
VECTORS = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))


def test_the_vector_file_is_well_formed():
    assert VECTORS["fallback"] == FALLBACK == "/"
    assert VECTORS["max_length"] == MAX_LENGTH == 2048
    nexts = [v["next"] for v in VECTORS["vectors"]]
    assert len(nexts) == len(set(nexts))
    assert all(v["expect"] in (v["next"], FALLBACK) for v in VECTORS["vectors"])
    accepted = [v for v in VECTORS["vectors"] if v["expect"] != FALLBACK]
    rejected = [v for v in VECTORS["vectors"] if v["expect"] == FALLBACK]
    assert len(accepted) >= 10 and len(rejected) >= 40
    # The file is ASCII JSON, so every runtime reads the same strings.
    VECTOR_FILE.read_bytes().decode("ascii")


@pytest.mark.parametrize(
    "vector", VECTORS["vectors"], ids=[v["why"] for v in VECTORS["vectors"]]
)
def test_redirect_vector(vector):
    assert validate_next(vector["next"]) == vector["expect"]


@pytest.mark.parametrize("value", VECTORS["non_strings"] + [b"/weeks", 1.5])
def test_non_strings_fall_back(value):
    assert validate_next(value) == FALLBACK


def test_the_required_vectors_are_present():
    nexts = {v["next"]: v["expect"] for v in VECTORS["vectors"]}
    for required in (
        "/\t/evil.com",
        "/%09/evil.com",
        "//evil",
        "/\\evil",
        "/%2F%2Fevil",
        "/%5Cevil",
        "javascript:x",
        "/login",
        "/login?next=/",
        "/api/x",
        "/%00",
        "/%7F",
        "/%zz",
        "/" + "a" * 2048,
    ):
        assert nexts[required] == FALLBACK, required
    assert nexts["/" + "a" * 2047] == "/" + "a" * 2047
