from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

IDENT = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(cwd, *args) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **IDENT},
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def commit_file(repo, name, text, msg="c") -> None:
    p = Path(repo) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", msg)


def make_repo(root: Path, extra_branches=()) -> tuple[Path, Path]:
    """A bare origin plus a clone with one commit on main (pushed). Returns (origin, clone)."""
    origin, clone = root / "origin.git", root / "clone"
    git(root, "init", "--bare", "-b", "main", str(origin))
    git(root, "clone", str(origin), str(clone))
    git(clone, "checkout", "-b", "main")
    commit_file(clone, "README", "hi\n")
    git(clone, "push", "-u", "origin", "main")
    for b in extra_branches:
        git(clone, "push", "origin", f"main:{b}")
    return origin, clone


def tmpdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="sf-git-")
