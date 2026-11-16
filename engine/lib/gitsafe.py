"""Every git call sf makes in a ticket clone goes through here.

sf runs outside the sandbox, but sandboxed code can write a clone's .git. So git runs with hooks, fsmonitor,
pagers, ssh commands, credential helpers and ext:: transports pinned from the command line, and the clone's
.git/config must still match what sf recorded when it made the clone.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import state
from .proc import ProcError, run

# git writes these during ordinary branch work; sf never reads them, so they may change
_MUTABLE = re.compile(r"branch\..+\.(remote|merge)|user\.(name|email)")
_NO_VERIFY = {"push", "commit", "merge"}


class ConfigChanged(Exception):
    pass


def _env() -> dict:
    return {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def flags() -> list[str]:
    # read from "/" so no repo config takes part; the user's own global helper keeps HTTPS pushes working
    helpers = run(["git", "config", "--global", "--get-all", "credential.helper"], cwd="/", env=_env(),
                  check=False).splitlines()
    pinned = ["core.hooksPath=/dev/null", "core.fsmonitor=false", "core.sshCommand=ssh", "credential.helper=",
              *(f"credential.helper={h}" for h in helpers if h), "core.pager=cat", "protocol.ext.allow=never",
              "submodule.recurse=false", "fetch.recurseSubmodules=false"]
    return [x for kv in pinned for x in ("-c", kv)]


def raw(cwd, *args: str, check: bool = True) -> str:
    """git with the pinned flags but no config check: for the main checkout and clones sf is still building."""
    args = list(args)
    if args and args[0] in _NO_VERIFY and not {"--abort", "--continue", "--quit"} & set(args):
        args.insert(1, "--no-verify")
    return run(["git", *flags(), "-C", str(cwd), *args], env=_env(), check=check).strip()


def state_dir(clone: Path) -> Path:
    """<state>/worktrees/<KEY>/<repo> -> <state>."""
    clone = Path(clone)
    if clone.parent.parent.name != "worktrees":
        raise ValueError(f"{clone} is not a crew clone")
    return clone.parent.parent.parent


def _pin_path(clone: Path) -> Path:
    clone = Path(clone)
    return state_dir(clone) / "clones" / clone.parent.name / f"{clone.name}.sha256"


def digest(clone: Path) -> str:
    gitdir = Path(clone) / ".git"
    cfg = gitdir / "config"
    if gitdir.is_symlink() or not gitdir.is_dir() or cfg.is_symlink():
        raise ConfigChanged(f"{clone}/.git is not a plain clone directory; refusing")
    try:
        # --file reads that file alone: includes are listed, never followed
        out = run(["git", "config", "--file", str(cfg), "--list", "--null"], cwd="/", env=_env())
    except ProcError as e:
        raise ConfigChanged(f"can't read {cfg}: {e}") from None
    kept = [e for e in out.split("\0") if e and not _MUTABLE.fullmatch(e.partition("\n")[0])]
    return hashlib.sha256("\0".join(kept).encode()).hexdigest()


def pin(clone: Path, value: str | None = None) -> None:
    state.atomic_write(_pin_path(clone), (value or digest(clone)) + "\n")


def unpin(clone: Path) -> None:
    try:
        _pin_path(clone).unlink()
    except FileNotFoundError:
        pass


def pinned(clone: Path) -> bool:
    return _pin_path(clone).is_file()


def verify(clone: Path) -> None:
    clone = Path(clone)
    try:
        want = _pin_path(clone).read_text().strip()
    except FileNotFoundError:
        raise ConfigChanged(f"sf has no record of {clone}; refusing to run git there") from None
    try:
        ok = digest(clone) == want
    except ConfigChanged:
        ok = False
    if not ok:
        state.log_run(state_dir(clone).name, clone.parent.name, "refused", "clone config changed; refusing")
        raise ConfigChanged(f"clone config changed in {clone}; refusing to run git there. "
                            f"Check {clone}/.git/config, then remove the clone so sf can make a fresh one")


def git(clone: Path, *args: str, check: bool = True) -> str:
    verify(clone)
    return raw(clone, *args, check=check)


def push(clone: Path, main: str, branch: str, force: bool = False) -> None:
    """Push HEAD to <branch> at the main checkout's push URL; the clone's remote config plays no part."""
    url = raw(main, "remote", "get-url", "--push", "origin")
    lease = []
    if force:
        # a rebased ticket branch needs a force push; only over what sf last saw on origin
        seen = git(clone, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}", check=False)
        lease = [f"--force-with-lease=refs/heads/{branch}:{seen}"]
    git(clone, "push", "--quiet", *lease, url, f"HEAD:refs/heads/{branch}")
    git(clone, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
