"""The per-crew transport repos: the only repos sf fetches into or pushes from.

<state>/transport/<repo>.git is a bare repo sf makes and alone writes. Its refs/heads mirror origin as sf last
saw it, and refs/crew/<branch> holds what a crew handed over in a bundle. Code moves between it and a crew's
clone only as bundle files in the ticket folder, so sf never runs git where a crew can write.
"""
from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

from . import config, crewfs, gitsafe, paths

LOCAL_TIMEOUT = 60  # seconds for a bundle check or fetch; a crew-made bundle must not stall sf
NO_REMOTE = "file:///nonexistent"  # a crew clone's origin: sandboxed network git has nowhere to go


class TransportError(Exception):
    pass


def root(crew: str) -> Path:
    return paths.crew_state_dir(crew) / "transport"


def gitdir(crew: str, repo: str) -> Path:
    return root(crew) / f"{repo}.git"


def _t(cfg: dict, repo: str, *args: str, check: bool = True, local: bool = False,
       timeout: float = gitsafe.NET_TIMEOUT) -> str:
    return gitsafe.git(gitdir(cfg["id"], repo), *args, bare=True, local=local, check=check, timeout=timeout)


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


def publish(src: Path, crew: str, key: str, name: str) -> Path:
    """Move `src` into the ticket folder as `name`, replacing (never writing through) whatever is there."""
    crewfs.move_in(src, paths.tickets_dir(crew), f"{paths.check(paths.TICKET_KEY_RE, key, 'ticket key')}/{name}")
    return paths.ticket_dir(crew, key) / name


def bundle_max(cfg: dict) -> int:
    return (cfg.get("limits") or {}).get("bundleMaxMB", config.DEFAULTS["limits"]["bundleMaxMB"]) << 20


def take(cfg: dict, key: str, name: str, out: Path) -> Path:
    """Copy a crew-made bundle out of the ticket folder into `out`: a regular file under limits.bundleMaxMB,
    checked before a byte is copied."""
    rel = f"{paths.check(paths.TICKET_KEY_RE, key, 'ticket key')}/{name}"
    try:
        return crewfs.copy_out(paths.tickets_dir(cfg["id"]), rel, out, bundle_max(cfg))
    except FileNotFoundError:
        raise TransportError(f"no {name} in {key}'s ticket folder") from None
    except crewfs.CrewFsError as e:
        raise TransportError(f"refusing {name}: {e}") from None


def receive(cfg: dict, key: str, repo: str, branch: str) -> str:
    """Take <branch>, and nothing else, from the crew's out-<repo>.bundle into refs/crew/<branch>."""
    ensure(cfg, repo)
    name = f"out-{repo}.bundle"
    with scratch(cfg["id"]) as tmp:
        b = take(cfg, key, name, tmp / "out.bundle")
        _t(cfg, repo, "bundle", "verify", "--quiet", str(b), local=True, timeout=LOCAL_TIMEOUT)
        heads = {line.split()[-1] for line in
                 _t(cfg, repo, "bundle", "list-heads", str(b), local=True, timeout=LOCAL_TIMEOUT).splitlines()}
        if f"refs/heads/{branch}" not in heads:
            raise TransportError(f"{name} does not carry {branch}; run git bundle create <dir>/{name} {branch}")
        _t(cfg, repo, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", str(b),
           f"+refs/heads/{branch}:refs/crew/{branch}", local=True, timeout=LOCAL_TIMEOUT)
    return sha(cfg, repo, f"refs/crew/{branch}")


def push(cfg: dict, repo: str, branch: str, force: bool = False) -> None:
    """Push refs/crew/<branch> to origin's <branch>. A force push leases on the sha sf last saw on origin."""
    seen = sha(cfg, repo, f"refs/heads/{branch}")
    lease = [f"--force-with-lease=refs/heads/{branch}:{seen}"] if force else []
    _t(cfg, repo, "push", "--quiet", "--receive-pack=git-receive-pack", *lease, "origin",
       f"refs/crew/{branch}:refs/heads/{branch}")
    _t(cfg, repo, "update-ref", f"refs/heads/{branch}", f"refs/crew/{branch}", local=True)
