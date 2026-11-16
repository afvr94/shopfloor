from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import crewfs, gitsafe, paths, transport

SLUG_MAX = 40


def branch_name(key: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().encode("ascii", "ignore").decode()).strip("-")
    slug = slug[:SLUG_MAX].rstrip("-")
    return f"shopfloor/{key.lower()}" + (f"-{slug}" if slug else "")


def prepare(cfg: dict, key: str, repo_name: str, branch: str) -> Path:
    """Refresh <ticket>/in-<repo>.bundle from origin and, if the ticket has no clone yet, make one from it on
    `branch`. An existing clone belongs to the crew: sf leaves it alone."""
    paths.ticket_dir(cfg["id"], key)  # validates the key
    wts, rel = paths.worktrees_dir(cfg["id"]), f"{key}/{repo_name}"
    base = cfg["repos"][repo_name]["base"]
    exists = crewfs.exists(wts, rel)  # refuses a link at worktrees/<KEY> before sf does anything
    transport.update(cfg, repo_name)
    with transport.scratch(cfg["id"]) as tmp:
        b = transport.bundle_in(cfg, repo_name, branch, tmp / "in.bundle")
        if not exists:
            # built where crews can't write, then moved into place whole
            work = tmp / "clone"
            on_origin = bool(transport.sha(cfg, repo_name, f"refs/heads/{branch}"))
            gitsafe.git(tmp, "clone", "--quiet", "-b", branch if on_origin else base, str(b), str(work), local=True)
            if not on_origin:
                gitsafe.git(work, "checkout", "--quiet", "--no-track", "-b", branch, f"origin/{base}", local=True)
            gitsafe.git(work, "remote", "set-url", "origin", transport.NO_REMOTE, local=True)
            crewfs.move_in(work, wts, rel, replace=False)
        transport.publish(b, cfg["id"], key, f"in-{repo_name}.bundle")
    return wts / rel


def remove(cfg: dict, key: str) -> None:
    paths.ticket_dir(cfg["id"], key)
    shutil.rmtree(paths.worktrees_dir(cfg["id"]) / key, ignore_errors=True)
