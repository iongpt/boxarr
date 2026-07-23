"""Atomic JSON file writes.

Writing ``config/weekly_pages/*.json`` (and scheduler history) with a plain
``open(path, "w")`` + ``json.dump`` leaves a truncated file if the process is
interrupted mid-write, which readers then fail to parse. ``atomic_write_json``
writes to a temporary file in the same directory and ``os.replace()``s it into
place — an atomic rename on POSIX and Windows — so readers only ever observe a
complete file.
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
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
