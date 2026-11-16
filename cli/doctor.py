from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.request
from urllib.parse import quote

from engine.adapters.chat import make_chat
from engine.adapters.tracker import make_tracker
from engine.hooks.guard import decide
from engine.lib import config, paths, proc
from engine.lib.redact import redact

Row = tuple[bool, str, str]
SANDBOX_HINT = {
    "darwin": (["sandbox-exec"], "sandbox-exec ships with macOS; check /usr/bin is on PATH"),
    "linux": (["bwrap", "socat"], "install them, e.g. apt install bubblewrap socat"),
}


def _health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def _guarded(name: str, fn) -> Row:
    """Run a check returning a problem string or None; an exception is a failure, never a crash."""
    try:
        problem = fn()
    except Exception as e:  # noqa: BLE001 - every failure becomes a row
        problem = str(e) or type(e).__name__
    return (not problem, name, redact(problem or ""))


def _secret_row(label: str, path: str) -> Row:
    def check():
        try:
            mode = os.stat(os.path.expanduser(path)).st_mode
        except OSError:
            return f"{path} is missing or unreadable"
        return f"{path} must be mode 0600 (chmod 600 it)" if mode & 0o077 else None
    return _guarded(f"{label} secret", check)


def _repo_rows(cid: str, cfg: dict, deps: dict) -> list[Row]:
    rows: list[Row] = []
    for name, r in cfg["repos"].items():
        def origin(r=r):
            url = deps["run"](["git", "-C", r["path"], "remote", "get-url", "origin"]).strip()
            if not re.search(rf"github\.com[:/]{re.escape(r['github'])}(\.git)?/?$", url):
                return f"origin does not match {r['github']}"
        rows.append(_guarded(f"{cid}: repo {name}", origin))
        for branch in cfg["protectedBranches"]:
            def protected(r=r, branch=branch):
                try:
                    out = deps["run"](["gh", "api", f"repos/{r['github']}/branches/{quote(branch, safe='')}"])
                except proc.ProcError as e:
                    return None if "404" in str(e) else str(e)  # absent on the remote: nothing to protect
                if not json.loads(out).get("protected"):
                    return (f"{r['github']}:{branch} is not protected. Settings → Branches → add a rule for "
                            f"{branch}, or turn on a ruleset")
            rows.append(_guarded(f"{cid}: branch protection {branch} ({name})", protected))
    return rows


def _crew_rows(cid: str, deps: dict) -> list[Row]:
    try:
        cfg = config.load_crew(cid)
    except config.ConfigError as e:
        return [(False, f"{cid}: config", redact(str(e)))]
    rows: list[Row] = [(True, f"{cid}: config", "")]
    rows += [_secret_row(f"{cid}: {k}", cfg[k]["tokenFile"]) for k in ("tracker", "chat")]
    tracker, chat = deps["tracker"](cfg), deps["chat"](cfg)
    rows.append(_guarded(f"{cid}: linear viewer", lambda: tracker.viewer() and None))
    rows.append(_guarded(f"{cid}: linear team", lambda: "; ".join(tracker.team_check()) or None))
    rows.append(_guarded(f"{cid}: slack auth", lambda: chat.auth_test() and None))
    rows.append(_guarded(f"{cid}: slack channel", chat.channel_check))
    rows += _repo_rows(cid, cfg, deps)
    rows.append(_guarded(f"{cid}: guard self-test", lambda: None if decide(
        "Bash", {"command": "git push origin main"}, str(paths.shopfloor_home()), cfg) else "git push origin main was allowed"))
    missing = [d for d in ("worktrees", "tickets") if not (paths.crew_dir(cid) / d).is_dir()]
    rows.append((not missing, f"{cid}: crew dirs", f"missing {', '.join(missing)} (run shopfloor install)" if missing else ""))
    return rows


def check_all(crew_id: str | None, deps: dict | None = None, during_init: bool = False) -> list[Row]:
    d = {"which": shutil.which, "run": proc.run, "tracker": make_tracker, "chat": make_chat, "health": _health}
    d.update(deps or {})
    which = d["which"]
    rows: list[Row] = [
        (sys.platform in ("darwin", "linux"), "platform", sys.platform),
        (sys.version_info >= (3, 9), "python", "needs 3.9 or newer" if sys.version_info < (3, 9) else ""),
    ]
    rows += [(bool(which(t)), t, "" if which(t) else f"{t} not found on PATH") for t in ("claude", "git", "gh")]
    rows.append(_guarded("gh auth", lambda: d["run"](["gh", "auth", "status"]) and None))
    tools, hint = SANDBOX_HINT.get(sys.platform, ([], ""))
    absent = [t for t in tools if not which(t)]
    rows.append((not absent, "sandbox", f"{', '.join(absent)} missing: {hint}" if absent else ""))
    try:
        board = config.load_board()
        rows.append((True, "board config", ""))
        crews = [crew_id] if crew_id else config.list_crews()
    except config.ConfigError as e:
        return rows + [(False, "board config", redact(str(e)))]
    for cid in crews:
        rows += _crew_rows(cid, d)
    url = f"http://127.0.0.1:{board['port']}/healthz"
    up = d["health"](url)
    rows.append((up or during_init, "board", "" if up else f"not answering at {url}" + (" (warning)" if during_init else "")))
    return rows


def report(rows: list[Row], out=print) -> int:
    for ok, name, detail in rows:
        out(f"✓ {name}" if ok else f"✗ {name}: {detail}")
    return 0 if all(ok for ok, _, _ in rows) else 1
