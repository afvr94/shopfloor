from __future__ import annotations

import datetime
import os
import re
import shutil
from pathlib import Path

from . import paths
from .proc import ProcError, run

SLUG_MAX = 40


def branch_name(key: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().encode("ascii", "ignore").decode()).strip("-")
    slug = slug[:SLUG_MAX].rstrip("-")
    return f"shopfloor/{key.lower()}" + (f"-{slug}" if slug else "")


def _git(path, *args, check=True) -> str:
    return run(["git", "-C", str(path), *args], check=check).strip()


def _common_dir(path) -> str:
    out = _git(path, "rev-parse", "--git-common-dir", check=False)
    return os.path.realpath(os.path.join(str(path), out)) if out else ""


def _healthy(wt: Path, repo_path: str, branch: str) -> bool:
    if not (wt / ".git").exists() or _common_dir(wt) != _common_dir(repo_path):
        return False
    return _git(wt, "symbolic-ref", "--short", "-q", "HEAD", check=False) == branch


def _drop(wt: Path, repo_path: str) -> None:
    try:
        _git(repo_path, "worktree", "remove", "--force", str(wt))
    except ProcError:
        shutil.rmtree(wt, ignore_errors=True)
    _git(repo_path, "worktree", "prune", check=False)


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _salvage(wt: Path, crew_id: str, key: str, repo_name: str) -> None:
    try:
        _git(wt, "add", "-A", "-N", ".")
        patch = _git(wt, "diff", "HEAD", "--binary")
    except ProcError:
        return
    if not patch:
        return
    d = paths.ticket_dir(crew_id, key)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"salvage-{repo_name}-{_stamp()}.patch").write_text(patch + "\n")


def _move_aside(wt: Path, crew_id: str, key: str, repo_name: str) -> None:
    d = paths.ticket_dir(crew_id, key)
    d.mkdir(parents=True, exist_ok=True)
    shutil.move(str(wt), str(d / f"salvage-{repo_name}-{_stamp()}"))


def prepare(cfg: dict, key: str, repo_name: str, branch: str) -> Path:
    repo = cfg["repos"][repo_name]
    main = repo["path"]
    paths.ticket_dir(cfg["id"], key)  # validates the key
    wt = paths.worktrees_dir(cfg["id"]) / key / repo_name
    _git(main, "fetch", "origin")
    if wt.exists():
        if _healthy(wt, main, branch):
            return wt
        if (wt / ".git").exists():
            _salvage(wt, cfg["id"], key, repo_name)
            _drop(wt, main)
        else:
            _move_aside(wt, cfg["id"], key, repo_name)
    _git(main, "worktree", "prune", check=False)
    wt.parent.mkdir(parents=True, exist_ok=True)

    def has(ref: str) -> bool:
        return bool(_git(main, "rev-parse", "--verify", "-q", ref, check=False))

    if has(f"refs/heads/{branch}") and (
        not has(f"refs/remotes/origin/{branch}")
        or _git(main, "rev-list", f"origin/{branch}..{branch}", check=False)
    ):
        # unpushed commits: attach to the local branch rather than reset it
        _git(main, "worktree", "add", str(wt), branch)
    elif has(f"refs/remotes/origin/{branch}"):
        _git(main, "worktree", "add", "-B", branch, str(wt), f"origin/{branch}")
    else:
        _git(main, "worktree", "add", "-b", branch, str(wt), f"origin/{repo['base']}")
    return wt


def remove(cfg: dict, key: str) -> None:
    paths.ticket_dir(cfg["id"], key)
    root = paths.worktrees_dir(cfg["id"]) / key
    for name, repo in cfg["repos"].items():
        wt = root / name
        if wt.exists():
            _drop(wt, repo["path"])
        _git(repo["path"], "worktree", "prune", check=False)
    shutil.rmtree(root, ignore_errors=True)
