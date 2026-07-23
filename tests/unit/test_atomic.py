"""Tests for the atomic JSON write helper."""

import json

import pytest

from src.utils import atomic
from src.utils.atomic import atomic_write_json


def test_atomic_write_json_writes_content(tmp_path):
    target = tmp_path / "sub" / "data.json"

    atomic_write_json(target, {"a": 1, "b": [2, 3]}, indent=2)

    assert target.exists()
    assert json.loads(target.read_text()) == {"a": 1, "b": [2, 3]}


def test_atomic_write_json_uses_os_replace(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    calls = []
    real_replace = atomic.os.replace

    def _spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", _spy_replace)

    atomic_write_json(target, {"ok": True})

    assert len(calls) == 1
    src_name, dst_name = calls[0]
    assert dst_name == str(target)
    assert src_name.endswith(".tmp")
    assert json.loads(target.read_text()) == {"ok": True}


def test_atomic_write_json_leaves_target_intact_on_failure(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_json(target, {"original": True})

    # object() is not JSON-serializable, so json.dump raises mid-write.
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})

    # The pre-existing file must be untouched (not truncated) ...
    assert json.loads(target.read_text()) == {"original": True}
    # ... and the aborted temp file must be cleaned up.
    assert list(tmp_path.glob("*.tmp")) == []
