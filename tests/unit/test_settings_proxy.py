"""Tests for the lazy settings proxy."""

import pytest

from src.utils.config import get_settings, settings


def test_monkeypatch_on_proxy_restores_original_value():
    """Patching an attribute through the proxy is undone cleanly.

    pytest 9.2 reads the value to restore from the target's ``__dict__``;
    the proxy must expose the real settings there or teardown deletes the
    field instead of restoring it.
    """
    original = get_settings().boxarr_features_box_office_limit

    mp = pytest.MonkeyPatch()
    mp.setattr(settings, "boxarr_features_box_office_limit", original + 5)
    assert settings.boxarr_features_box_office_limit == original + 5
    assert get_settings().boxarr_features_box_office_limit == original + 5
    mp.undo()

    assert settings.boxarr_features_box_office_limit == original
    assert get_settings().boxarr_features_box_office_limit == original
