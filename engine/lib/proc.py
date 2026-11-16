from __future__ import annotations

import os
import signal
import subprocess

from engine.lib.redact import redact

DRAIN_TIMEOUT = 5


class ProcError(Exception):
    """A command failed. `code` is the exit status, or None when it timed out."""

    def __init__(self, argv, code, stderr: str):
        self.code = code
        self.stderr = redact(stderr or "")
        super().__init__(f"{argv[0]} failed ({code}): {self.stderr.strip()[:500]}")


def _kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(argv: list[str], cwd=None, timeout=300, env=None, check=True, input: str | None = None) -> str:
    """`input`, when given, is the child's stdin: how text reaches a program without a file anyone else can swap."""
    # its own process group, so a timeout also kills helpers it started (ssh, remote-https)
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        try:
            p.communicate(timeout=DRAIN_TIMEOUT)  # a grandchild outside the group may still hold the pipes
        except subprocess.TimeoutExpired:
            pass
        raise ProcError(argv, None, f"timed out after {timeout:g}s and was stopped") from None
    except BaseException:
        _kill_group(p)
        p.wait()
        raise
    if check and p.returncode != 0:
        raise ProcError(argv, p.returncode, err)
    return out
