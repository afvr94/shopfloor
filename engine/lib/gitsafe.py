"""Every git command sf runs goes through git() here.

sf runs outside the sandbox, so it never runs git, or anything that reads git config, inside a directory a crew
can write: a repo there could redirect git through its own .git (config, commondir, a parent repo) and run code
as sf. git() refuses such a directory as its target or as a path argument. The places it does run are sf's own:
the transport repos and merge copies under the crew state dir, private scratch dirs there, and the owner's
checkout. Hooks, fsmonitor, pagers, ssh commands, credential helpers and ext:: transports are pinned on the
command line all the same.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import paths
from .proc import run

_NO_VERIFY = {"push", "commit", "merge"}
_CREW_DIRS = {"worktrees", "tickets"}


class CrewWritable(Exception):
    pass


def crew_writable(p) -> bool:
    """True for any path inside some crew's worktrees or tickets folder, compared as macOS does (any case)."""
    real = os.path.realpath(p).casefold()
    state = os.path.realpath(paths.shopfloor_home() / "state").casefold().rstrip("/") + "/"
    if not real.startswith(state):
        return False
    parts = real[len(state):].split("/")
    return len(parts) > 1 and parts[1] in _CREW_DIRS


def _refuse(p) -> None:
    if crew_writable(p):
        raise CrewWritable(f"sf never runs git in or on {p}: crews can write there")


def flags() -> list[str]:
    # read from "/" so no repo config takes part; the user's own global helper keeps HTTPS pushes working
    helpers = run(["git", "config", "--global", "--get-all", "credential.helper"], cwd="/",
                  env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}, check=False).splitlines()
    pinned = ["core.hooksPath=/dev/null", "core.fsmonitor=false", "core.sshCommand=ssh", "credential.helper=",
              *(f"credential.helper={h}" for h in helpers if h), "core.pager=cat", "protocol.ext.allow=never",
              "submodule.recurse=false", "fetch.recurseSubmodules=false"]
    return [x for kv in pinned for x in ("-c", kv)]


def git(where, *args: str, bare: bool = False, local: bool = False, check: bool = True) -> str:
    """git in `where`, an sf-only directory: a bare repo when `bare` (GIT_DIR is set, nothing is discovered),
    else a work tree, a repo's parent or the owner's checkout. `local` drops the user's global config too, for
    commands that only move data between sf's own repos and bundles."""
    where = Path(where)
    _refuse(where)
    for a in args:
        if a.startswith("/"):
            _refuse(a)
    args = list(args)
    if args and args[0] in _NO_VERIFY and not {"--abort", "--continue", "--quit"} & set(args):
        args.insert(1, "--no-verify")
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
           "GIT_CEILING_DIRECTORIES": str(where.parent)}
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(k, None)
    if bare:
        env["GIT_DIR"] = str(where)
    if local:
        env["GIT_CONFIG_GLOBAL"] = os.devnull
    return run(["git", *flags(), *args], cwd=str(where), env=env, check=check).strip()
