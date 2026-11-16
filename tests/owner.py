"""The owner editing sf's locked records by hand in a test: open the folder the way sf does, then write."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

from engine.lib import state


def write(path, text: str, mode: str = "w") -> None:
    path = Path(path)
    state.make_dir(path.parent)
    with state.unlocked(path.parent) as declared:
        declared[path.name] = None
        with contextlib.suppress(FileNotFoundError):
            os.chmod(path, 0o600)
        with open(path, mode) as f:
            f.write(text)
        os.chmod(path, 0o400)


def append(path, text: str) -> None:
    write(path, text, "a")
