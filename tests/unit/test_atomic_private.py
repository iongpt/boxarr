"""atomic_write_private: atomic like atomic_write_json, but always 0600 (#151).

local.yaml holds the Radarr API key and the auth files hold credential
hashes, so the file a write leaves behind must be readable by its owner only,
whatever the umask and whatever mode the file it replaces had.
"""

import os
import stat

import pytest

from src.utils import atomic
from src.utils.atomic import atomic_write_private


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask():
    """Set a umask for the test, and put the old one back."""
    saved = os.umask(0o022)
    os.umask(saved)

    def _set(value: int) -> None:
        os.umask(value)

    yield _set
    os.umask(saved)


def test_writes_the_text(tmp_path):
    target = tmp_path / "sub" / "auth.json"

    atomic_write_private(target, '{"version": 1}\n')

    assert target.read_text(encoding="utf-8") == '{"version": 1}\n'


@pytest.mark.parametrize("value", [0o000, 0o022, 0o077])
def test_the_file_is_0600_whatever_the_umask(tmp_path, umask, value):
    umask(value)
    target = tmp_path / "local.yaml"

    atomic_write_private(target, "radarr: {}\n")

    assert _mode(target) == 0o600


def test_a_0644_file_is_replaced_by_a_0600_one(tmp_path):
    target = tmp_path / "local.yaml"
    target.write_text("old\n")
    target.chmod(0o644)

    atomic_write_private(target, "new\n")

    assert target.read_text() == "new\n"
    assert _mode(target) == 0o600


def test_it_swaps_the_file_in_with_os_replace(tmp_path, monkeypatch):
    target = tmp_path / "auth.json"
    calls = []
    real_replace = atomic.os.replace

    def _spy(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", _spy)

    atomic_write_private(target, "{}")

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(target)
    assert src.endswith(".tmp")
    assert os.path.dirname(src) == str(tmp_path)


def test_a_failed_swap_leaves_the_old_file_and_no_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "auth.json"
    atomic_write_private(target, "original")

    def _boom(*_args, **_kwargs):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(atomic.os, "replace", _boom)

    with pytest.raises(OSError):
        atomic_write_private(target, "replacement")

    assert target.read_text() == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_unicode_is_written_as_utf8(tmp_path):
    target = tmp_path / "auth.json"

    atomic_write_private(target, "Zoë\n")

    assert target.read_bytes() == "Zoë\n".encode("utf-8")
