"""The per-crew transport repos: the only repos sf fetches into or pushes from.

<state>/transport/<repo>.git is a bare repo sf makes and alone writes. Its refs/heads mirror origin as sf last
saw it, and refs/crew/<branch> holds what a crew handed over in a bundle. Code moves between it and a crew's
clone only as bundle files in the ticket folder, so sf never runs git where a crew can write.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from . import gitsafe, paths

NO_REMOTE = "file:///nonexistent"  # a crew clone's origin: sandboxed network git has nowhere to go


class TransportError(Exception):
    pass


def root(crew: str) -> Path:
    return paths.crew_state_dir(crew) / "transport"


def gitdir(crew: str, repo: str) -> Path:
    return root(crew) / f"{repo}.git"


def _t(cfg: dict, repo: str, *args: str, check: bool = True, local: bool = False) -> str:
    return gitsafe.git(gitdir(cfg["id"], repo), *args, bare=True, local=local, check=check)


def ensure(cfg: dict, repo: str) -> Path:
    """The transport repo for `repo`, made on first use; origin follows the owner's checkout."""
    d = gitdir(cfg["id"], repo)
    main = cfg["repos"][repo]["path"]
    url = gitsafe.git(main, "remote", "get-url", "origin")
    push_url = gitsafe.git(main, "remote", "get-url", "--push", "origin")
    if not (d / "HEAD").is_file():
        d.mkdir(parents=True, exist_ok=True)
        _t(cfg, repo, "init", "--quiet", "--bare", local=True)
    _t(cfg, repo, "config", "remote.origin.url", url, local=True)
    _t(cfg, repo, "config", "--replace-all", "remote.origin.fetch", "+refs/heads/*:refs/heads/*", local=True)
    if push_url != url:
        _t(cfg, repo, "config", "remote.origin.pushurl", push_url, local=True)
    else:
        _t(cfg, repo, "config", "--unset-all", "remote.origin.pushurl", local=True, check=False)
    return d


def update(cfg: dict, repo: str) -> None:
    ensure(cfg, repo)
    _t(cfg, repo, "fetch", "--quiet", "--prune", "--no-tags", "--upload-pack=git-upload-pack", "origin")


def sha(cfg: dict, repo: str, ref: str) -> str:
    return _t(cfg, repo, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False, local=True)


@contextlib.contextmanager
def scratch(crew: str):
    """A private dir under the crew state dir, outside the sandbox's write roots."""
    root(crew).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root(crew), prefix="tmp-") as d:
        yield Path(d)


def bundle_in(cfg: dict, repo: str, branch: str, out: Path) -> Path:
    """origin/<base>, and origin/<branch> if it exists, as refs/heads/* in a bundle at `out`."""
    base = cfg["repos"][repo]["base"]
    if not sha(cfg, repo, f"refs/heads/{base}"):
        raise TransportError(f"origin has no {base} branch")
    refs = [f"refs/heads/{base}"] + ([f"refs/heads/{branch}"] if sha(cfg, repo, f"refs/heads/{branch}") else [])
    _t(cfg, repo, "bundle", "create", "--quiet", str(out), *refs, local=True)
    return out


def _ticket_fd(crew: str, key: str) -> int:
    d = paths.ticket_dir(crew, key)
    d.parent.mkdir(parents=True, exist_ok=True)
    d.mkdir(exist_ok=True)
    # O_NOFOLLOW: a crew may have swapped its ticket folder for a symlink
    return os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def publish(src: Path, crew: str, key: str, name: str) -> Path:
    """Move `src` into the ticket folder as `name`, replacing (never writing through) whatever is there."""
    fd = _ticket_fd(crew, key)
    try:
        os.rename(src, name, dst_dir_fd=fd)
    finally:
        os.close(fd)
    return paths.ticket_dir(crew, key) / name


def take(crew: str, key: str, name: str, out: Path) -> Path:
    """Copy a crew-made file from the ticket folder into `out`: a regular file only, never through a symlink."""
    fd = _ticket_fd(crew, key)
    try:
        src = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        raise TransportError(f"no {name} in {key}'s ticket folder") from None
    finally:
        os.close(fd)
    with os.fdopen(src, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise TransportError(f"{name} is not a regular file")
        with open(out, "wb") as o:
            shutil.copyfileobj(f, o)
    return out


def receive(cfg: dict, key: str, repo: str, branch: str) -> str:
    """Take <branch>, and nothing else, from the crew's out-<repo>.bundle into refs/crew/<branch>."""
    ensure(cfg, repo)
    name = f"out-{repo}.bundle"
    with scratch(cfg["id"]) as tmp:
        b = take(cfg["id"], key, name, tmp / "out.bundle")
        _t(cfg, repo, "bundle", "verify", "--quiet", str(b), local=True)
        heads = {line.split()[-1] for line in _t(cfg, repo, "bundle", "list-heads", str(b), local=True).splitlines()}
        if f"refs/heads/{branch}" not in heads:
            raise TransportError(f"{name} does not carry {branch}; run git bundle create <dir>/{name} {branch}")
        _t(cfg, repo, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", str(b),
           f"+refs/heads/{branch}:refs/crew/{branch}", local=True)
    return sha(cfg, repo, f"refs/crew/{branch}")


def push(cfg: dict, repo: str, branch: str, force: bool = False) -> None:
    """Push refs/crew/<branch> to origin's <branch>. A force push leases on the sha sf last saw on origin."""
    seen = sha(cfg, repo, f"refs/heads/{branch}")
    lease = [f"--force-with-lease=refs/heads/{branch}:{seen}"] if force else []
    _t(cfg, repo, "push", "--quiet", "--receive-pack=git-receive-pack", *lease, "origin",
       f"refs/crew/{branch}:refs/heads/{branch}")
    _t(cfg, repo, "update-ref", f"refs/heads/{branch}", f"refs/crew/{branch}", local=True)
