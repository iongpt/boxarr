"""Atomic file writes.

Writing ``config/weekly_pages/*.json`` (and scheduler history) with a plain
``open(path, "w")`` + ``json.dump`` leaves a truncated file if the process is
interrupted mid-write, which readers then fail to parse. ``atomic_write_json``
writes to a temporary file in the same directory and ``os.replace()``s it into
place — an atomic rename on POSIX and Windows — so readers only ever observe a
complete file.

``atomic_write_private`` does the same for files that hold a secret
(``local.yaml`` with the Radarr API key, the auth files): the result keeps the
0600 mode ``mkstemp`` gives the temp file, whatever the umask.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Union


def atomic_write_json(path: Union[str, Path], data: Any, **json_kwargs: Any) -> None:
    """Serialize ``data`` to ``path`` atomically via a same-directory temp file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with open(tmp_fd, "w") as handle:
            json.dump(data, handle, **json_kwargs)
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates the temp file 0600; relax to the standard 0644
        # (honouring the process umask) so replaced files keep readable perms.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp_path, 0o666 & ~umask)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_private(path: Union[str, Path], text: str) -> None:
    """Write ``text`` to ``path`` atomically, readable by the owner only.

    The temp file comes from ``mkstemp`` in the target's directory, which
    creates it 0600; it is written, fsynced and ``os.replace``d over the
    target, so the target ends up 0600 too - a file that was 0644 before is
    replaced by a 0600 one, never loosened. On any failure the target is left
    as it was and the temp file is removed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with open(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
