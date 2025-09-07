"""Integration test to ensure Settings UI rehydrates root-folder mappings.

This test guards against a previously reported regression where, after enabling
and saving rules, navigating back to Settings showed the feature disabled and no
rules. The desired behavior is that the checkbox is checked and existing rules
are visible/loaded. The current implementation satisfies this and the test
should pass.
"""

from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.utils.config import Settings


def _seed_config(dir_path: Path) -> Path:
    cfg = {
        "radarr": {
            "url": "http://localhost:7878",
            "api_key": "test-key",
            "root_folder": "/movies",
            "quality_profile_default": "HD-1080p",
            "root_folder_config": {
                "enabled": True,
                "mappings": [
                    {
                        "genres": ["Horror", "Thriller"],
                        "root_folder": "/movies/horror",
                        "priority": 10,
                    }
                ],
            },
        },
        "boxarr": {
            "scheduler": {"enabled": False, "cron": "0 23 * * 1"},
            "features": {"auto_add": False, "quality_upgrade": True},
            "ui": {"theme": "light"},
        },
    }
    p = dir_path / "local.yaml"
    with open(p, "w") as f:
        yaml.safe_dump(cfg, f)
    return p


def test_setup_page_rehydrates_root_folder_mapping(tmp_path, monkeypatch):
    # Point Boxarr to tmp config directory and seed config with enabled mapping
    monkeypatch.setenv("BOXARR_DATA_DIRECTORY", str(tmp_path))
    config_path = _seed_config(tmp_path)

    # Force reload settings from file for this test
    Settings.reload_from_file(config_path)

    app = create_app()
    client = TestClient(app)

    resp = client.get("/setup")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "html.parser")

    # Desired: checkbox should be checked based on config
    checkbox = soup.find("input", {"id": "rootFolderMappingEnabled"})
    assert checkbox is not None
    # Expected FAIL: element currently lacks the 'checked' attribute
    assert checkbox.has_attr(
        "checked"
    ), "Root-folder mapping checkbox should be checked"

    # Desired: existing rules should be rendered (or empty state should not be shown)
    mappings_list = soup.find(id="mappingsList")
    assert mappings_list is not None
    # Expected FAIL: template currently shows the empty-state placeholder
    assert "No rules configured yet" not in mappings_list.get_text(
        " "
    ), "Existing mappings should appear in Settings instead of the empty state"
