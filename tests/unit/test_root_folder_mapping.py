"""Unit tests for genre→root-folder mapping logic.

These focus on ``Settings.get_root_folder_for_genres`` independent of Radarr.
The current implementation supports case-insensitive matching and priority
tie‑breaking; these tests verify that behavior.
"""

from src.utils.config import (
    RootFolderConfig,
    RootFolderMapping,
    Settings,
)


def make_settings_with_mappings(mappings: list[RootFolderMapping]) -> Settings:
    s = Settings()
    s.radarr_root_folder_config = RootFolderConfig(enabled=True, mappings=mappings)
    # Ensure a predictable default for assertions
    # Settings uses Path for radarr_root_folder; cast to str on compare in code
    return s


def test_order_first_top_to_bottom_wins():
    """Order-first: the first matching rule wins regardless of numeric priority."""
    mappings = [
        RootFolderMapping(
            genres=["War", "History", "Documentary"],
            root_folder="/movies/war-history",
            priority=0,
        ),
        RootFolderMapping(
            genres=["War", "Drama"], root_folder="/movies/war-drama", priority=99
        ),
    ]

    s = make_settings_with_mappings(mappings)
    # Both rules match "War"; top-most (index 0) should win
    assert s.get_root_folder_for_genres(["War"]) == "/movies/war-history"


def test_mapping_is_case_insensitive_expected_behavior():
    """Genre matching is case-insensitive.

    Verifies mapping logic normalizes both configured and input genres and
    selects the correct target folder regardless of case.
    """
    mappings = [
        RootFolderMapping(
            genres=["Science Fiction"],
            root_folder="/movies/scifi",
            priority=10,
        )
    ]
    s = make_settings_with_mappings(mappings)

    # Input uses different case; desired behavior is to still match.
    assert s.get_root_folder_for_genres(["science fiction"]) == "/movies/scifi"
