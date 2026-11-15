from __future__ import annotations

import subprocess

from engine.lib.redact import redact


class ProcError(Exception):
    """A command failed. `code` is the exit status, or None when it timed out."""

    def __init__(self, argv, code, stderr: str):
        self.code = code
        self.stderr = redact(stderr or "")
        super().__init__(f"{argv[0]} failed ({code}): {self.stderr.strip()[:500]}")


def run(argv: list[str], cwd=None, timeout=300, env=None, check=True) -> str:
    try:
        r = subprocess.run(
            argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, text=True
        )
    except subprocess.TimeoutExpired as e:
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else e.stderr
        raise ProcError(argv, None, err or "timed out") from None
    if check and r.returncode != 0:
        raise ProcError(argv, r.returncode, r.stderr)
    return r.stdout
