"""Tests for order-first semantics of genre→root-folder rules.

This encodes the desired behavior for the new model:
  - Rules are an ordered list (top → bottom)
  - Apply the first rule that matches and stop
  - Numeric "priority" is only a hidden persisted index; logic must not sort by it

The current implementation uses numeric priority (desc) and will FAIL this test.
"""

from src.utils.config import RootFolderConfig, RootFolderMapping, Settings


def test_first_matching_rule_wins_top_to_bottom():
    """When multiple rules match, the top-most rule should win.

    Current implementation sorts by numeric priority (desc), so it would
    incorrectly select the bottom rule with higher priority. This test
    documents the desired order-first behavior and should fail until the
    implementation is updated.
    """
    mappings = [
        RootFolderMapping(
            genres=["Horror"], root_folder="/movies/top", priority=1  # Top-most
        ),
        RootFolderMapping(
            genres=["Horror"],
            root_folder="/movies/bottom",
            priority=99,  # Higher numeric priority but should be ignored
        ),
    ]

    s = Settings()
    s.radarr_root_folder_config = RootFolderConfig(enabled=True, mappings=mappings)

    # Desired: first (top) matching rule wins
    assert s.get_root_folder_for_genres(["Horror"]) == "/movies/top"

