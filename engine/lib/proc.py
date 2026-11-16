from __future__ import annotations

import os
import signal
import subprocess

from engine.lib.redact import redact


class ProcError(Exception):
    """A command failed. `code` is the exit status, or None when it timed out."""

    def __init__(self, argv, code, stderr: str):
        self.code = code
        self.stderr = redact(stderr or "")
        super().__init__(f"{argv[0]} failed ({code}): {self.stderr.strip()[:500]}")


def run(argv: list[str], cwd=None, timeout=300, env=None, check=True) -> str:
    # its own process group, so a timeout also kills helpers it started (ssh, remote-https)
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        raise ProcError(argv, None, f"timed out after {timeout:g}s and was stopped") from None
    if check and p.returncode != 0:
        raise ProcError(argv, p.returncode, err)
    return out
