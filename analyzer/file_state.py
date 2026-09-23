"""Small file-state primitives shared by caches and delivery checkpoints."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def pipeline_lock(root: Path):
    # Both supported runtimes (macOS and the Linux VPS) provide flock. Keep the
    # inode: unlinking a lock file permits two processes to lock different files.
    import fcntl

    path = root / "output" / ".pipeline.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
