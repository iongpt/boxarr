"""Unit tests for genre→root-folder mapping logic.

These focus on Settings.get_root_folder_for_genres, independent of Radarr.
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


def test_mapping_selects_highest_priority_then_best_match_count():
    """Select mapping by priority, then by number of matching genres.

    - Two mappings match; highest priority wins.
    - If priorities tie, mapping with more matching genres wins.
    """
    mappings = [
        RootFolderMapping(genres=["Drama", "Romance"], root_folder="/movies/drama", priority=3),
        RootFolderMapping(
            genres=["War", "History", "Documentary"],
            root_folder="/movies/war-history",
            priority=5,
        ),
    ]

    s = make_settings_with_mappings(mappings)

    # Highest priority wins
    assert (
        s.get_root_folder_for_genres(["War", "Drama"]) == "/movies/war-history"
    )

    # Tie on priority: prefer mapping with more matching genres
    mappings_tie = [
        RootFolderMapping(genres=["Action", "Science Fiction"], root_folder="/movies/scifi", priority=5),
        RootFolderMapping(genres=["Action", "Science Fiction", "Fantasy"], root_folder="/movies/action-scifi", priority=5),
    ]
    s2 = make_settings_with_mappings(mappings_tie)
    # Second mapping has 3 matching genres vs 2 for the first
    assert s2.get_root_folder_for_genres(["Action", "Science Fiction", "Fantasy"]) == "/movies/action-scifi"


def test_mapping_is_case_insensitive_expected_behavior():
    """Expected behavior: genre matching should be case-insensitive.

    Current implementation is case-sensitive, so this test exposes the issue
    by asserting the desired behavior. It should FAIL until the matching logic
    normalizes case for both configured and input genres.
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
