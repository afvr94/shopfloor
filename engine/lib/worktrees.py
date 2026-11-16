from __future__ import annotations

import datetime
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


def clone(main: str, dest: Path) -> None:
    """A full local clone of the main checkout whose origin is the real remote, so its .git sits inside dest."""
    url = _git(main, "remote", "get-url", "origin")
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--quiet", "--no-checkout", str(main), str(dest)])
    _git(dest, "remote", "set-url", "origin", url)
    _git(dest, "fetch", "--prune", "origin")


def is_clone(d: Path, main: str) -> bool:
    return (d / ".git").is_dir() and \
        _git(d, "remote", "get-url", "origin", check=False) == _git(main, "remote", "get-url", "origin", check=False)


def _healthy(wt: Path, repo_path: str, branch: str) -> bool:
    return is_clone(wt, repo_path) and _git(wt, "symbolic-ref", "--short", "-q", "HEAD", check=False) == branch


def drop(wt: Path, main: str) -> None:
    shutil.rmtree(wt, ignore_errors=True)
    _git(main, "worktree", "prune", check=False)  # leftovers from the git-worktree layout


def _unpushed(wt: Path) -> bool:
    return bool(_git(wt, "rev-list", "-n1", "--branches", "--not", "--remotes=origin", check=False))


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
            _git(wt, "fetch", "--prune", "origin")
            return wt
        if (wt / ".git").exists():
            _salvage(wt, cfg["id"], key, repo_name)
            if _unpushed(wt):  # local commits live only in this clone: keep the whole directory
                _move_aside(wt, cfg["id"], key, repo_name)
            else:
                drop(wt, main)
        else:
            _move_aside(wt, cfg["id"], key, repo_name)

    def has(ref: str, where=main) -> bool:
        return bool(_git(where, "rev-parse", "--verify", "-q", ref, check=False))

    local = has(f"refs/heads/{branch}") and (
        not has(f"refs/remotes/origin/{branch}")
        or bool(_git(main, "rev-list", f"origin/{branch}..{branch}", check=False)))
    clone(main, wt)
    if local:
        # unpushed commits in the main checkout: carry the local branch over rather than reset it
        _git(wt, "fetch", "--quiet", "--update-head-ok", str(main), f"refs/heads/{branch}:refs/heads/{branch}")
        _git(wt, "checkout", "--quiet", branch)
        if has(f"refs/remotes/origin/{branch}", wt):
            _git(wt, "branch", "--quiet", "--set-upstream-to", f"origin/{branch}")
    elif has(f"refs/remotes/origin/{branch}", wt):
        _git(wt, "checkout", "--quiet", "-B", branch, f"origin/{branch}")
    else:
        _git(wt, "checkout", "--quiet", "--no-track", "-b", branch, f"origin/{repo['base']}")
    return wt


def remove(cfg: dict, key: str) -> None:
    paths.ticket_dir(cfg["id"], key)
    root = paths.worktrees_dir(cfg["id"]) / key
    for name, repo in cfg["repos"].items():
        drop(root / name, repo["path"])
    shutil.rmtree(root, ignore_errors=True)
