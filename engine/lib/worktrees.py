from __future__ import annotations

import datetime
import os
import re
import shutil
import tempfile
from pathlib import Path

from . import gitsafe, paths
from .proc import ProcError

SLUG_MAX = 40


def branch_name(key: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().encode("ascii", "ignore").decode()).strip("-")
    slug = slug[:SLUG_MAX].rstrip("-")
    return f"shopfloor/{key.lower()}" + (f"-{slug}" if slug else "")


_git = gitsafe.git  # inside a clone: config checked against what sf recorded
_main = gitsafe.raw  # the owner's own checkout


def clone(main: str, dest: Path) -> None:
    """A full local clone of the main checkout whose origin is the real remote, so its .git sits inside dest.
    It is built where crews can't write and its config is recorded before it moves into place."""
    url = _main(main, "remote", "get-url", "origin")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=gitsafe.state_dir(dest)) as tmp:
        work = Path(tmp) / "clone"
        _main("/", "clone", "--quiet", "--no-checkout", str(main), str(work))
        _main(work, "remote", "set-url", "origin", url)
        recorded = gitsafe.digest(work)
        os.rename(work, dest)
    gitsafe.pin(dest, recorded)
    _git(dest, "fetch", "--prune", "origin")


def is_clone(d: Path, main: str) -> bool:
    return (d / ".git").is_dir() and \
        _git(d, "remote", "get-url", "origin", check=False) == _main(main, "remote", "get-url", "origin", check=False)


def _healthy(wt: Path, repo_path: str, branch: str) -> bool:
    return is_clone(wt, repo_path) and _git(wt, "symbolic-ref", "--short", "-q", "HEAD", check=False) == branch


def drop(wt: Path, main: str) -> None:
    shutil.rmtree(wt, ignore_errors=True)
    gitsafe.unpin(wt)
    _main(main, "worktree", "prune", check=False)  # leftovers from the git-worktree layout


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
    gitsafe.unpin(wt)


def prepare(cfg: dict, key: str, repo_name: str, branch: str) -> Path:
    repo = cfg["repos"][repo_name]
    main = repo["path"]
    paths.ticket_dir(cfg["id"], key)  # validates the key
    wt = paths.worktrees_dir(cfg["id"]) / key / repo_name
    _main(main, "fetch", "origin")
    if wt.exists():
        if not gitsafe.pinned(wt):
            # not a clone sf made: running git in it could run whatever its config says, so keep it untouched
            _move_aside(wt, cfg["id"], key, repo_name)
            _main(main, "worktree", "prune", check=False)
        elif _healthy(wt, main, branch):
            _git(wt, "fetch", "--prune", "origin")
            return wt
        else:
            _salvage(wt, cfg["id"], key, repo_name)
            if _unpushed(wt):  # local commits live only in this clone: keep the whole directory
                _move_aside(wt, cfg["id"], key, repo_name)
            else:
                drop(wt, main)

    def has(ref: str, where=None) -> bool:
        g = _git if where else _main
        return bool(g(where or main, "rev-parse", "--verify", "-q", ref, check=False))

    local = has(f"refs/heads/{branch}") and (
        not has(f"refs/remotes/origin/{branch}")
        or bool(_main(main, "rev-list", f"origin/{branch}..{branch}", check=False)))
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
