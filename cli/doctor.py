from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from urllib.parse import quote

from engine.adapters.chat import make_chat
from engine.adapters.tracker import make_tracker
from engine.hooks.guard import decide
from engine.lib import config, gitsafe, paths, proc, runner, signing, state, transport
from engine.lib.redact import redact

Row = tuple[bool, str, str]
SMOKE_SECS = 300
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
            url = gitsafe.git(r["path"], "remote", "get-url", "origin")
            if not re.search(rf"github\.com[:/]{re.escape(r['github'])}(\.git)?/?$", url, re.I):
                return f"origin does not match {r['github']}"
        rows.append(_guarded(f"{cid}: repo {name}", origin))
        def visible(r=r):
            try:
                deps["run"](["gh", "api", f"repos/{r['github']}"])
            except proc.ProcError:
                return f"can't see {r['github']} with your gh login"
        seen = _guarded(f"{cid}: github repo {name}", visible)
        rows.append(seen)
        if not seen[0]:
            continue
        for branch in cfg["protectedBranches"]:
            def protected(r=r, branch=branch):
                try:
                    out = deps["run"](["gh", "api", f"repos/{r['github']}/branches/{quote(branch, safe='')}"])
                except proc.ProcError as e:
                    return None if "Branch not found" in str(e) else str(e)  # absent on the remote: nothing to protect
                if not json.loads(out).get("protected"):
                    return (f"{r['github']}:{branch} is not protected. Settings → Branches → add a rule for "
                            f"{branch}, or turn on a ruleset")
            rows.append(_guarded(f"{cid}: branch protection {branch} ({name})", protected))
    return rows


def live_smoke(cid: str) -> str | None:
    """One real sandboxed crew session in a temp dir: a sandboxed write there and an sf write outside it."""
    nonce = secrets.token_hex(4)
    argv, env = runner.claude_invocation(cid, (
        "This is a Shopfloor doctor check. Use the Bash tool to run these two commands, one Bash call each, "
        f"exactly as written, then reply DONE: `touch smoke-ok` and `sf runs log SMOKE-1 smoke-{nonce}`"))
    with tempfile.TemporaryDirectory(prefix="shopfloor-smoke-") as d:
        try:
            r = subprocess.run(argv, env=env, cwd=d, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=SMOKE_SECS)
        except subprocess.TimeoutExpired:
            return f"claude did not finish within {SMOKE_SECS}s"
        if r.returncode != 0:
            return f"claude exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}"
        if not os.path.exists(os.path.join(d, "smoke-ok")):
            return "the sandboxed write in the session's temp dir did not happen"
    if not any(x.get("result") == f"smoke-{nonce}" for x in state.recent_runs(cid)):
        return "sf could not write crew state from inside the session (is sf excluded from the sandbox?)"
    return None


def board_reach(cid: str) -> str | None:
    """A real sandboxed crew session tries to reach a stand-in for the board on 127.0.0.1, directly and through
    the sandbox proxy. It passes only if the session ran the probe and nothing reached the stand-in."""
    hits = []

    class Probe(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Probe)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    urls = [f"http://127.0.0.1:{port}/healthz", f"http://localhost:{port}/healthz"]
    probe = "; ".join([f"curl -s -m 5 -o /dev/null {u}; echo rc=$? >> reach.txt" for u in urls] +
                      [f'curl -s -m 5 -o /dev/null -x "$HTTP_PROXY" {urls[0]}; echo rc=$? >> reach.txt'])
    argv, env = runner.claude_invocation(cid, (
        "This is a Shopfloor doctor check of the sandbox's network rules. Use the Bash tool once to run this "
        f"exactly as written, then reply DONE: `{probe}`"))
    try:
        with tempfile.TemporaryDirectory(prefix="shopfloor-reach-") as d:
            try:
                r = subprocess.run(argv, env=env, cwd=d, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   timeout=SMOKE_SECS)
            except subprocess.TimeoutExpired:
                return f"claude did not finish within {SMOKE_SECS}s"
            try:
                ran = open(os.path.join(d, "reach.txt")).read().split()
            except OSError:
                ran = []
    finally:
        server.shutdown()
        server.server_close()
    if hits:
        return f"a sandboxed command reached 127.0.0.1:{port} ({len(hits)} requests): crews could reach the board"
    if len(ran) != 3:
        return f"the session did not run the probe (claude exited {r.returncode}); can't tell"
    return None


def ls_remote(cfg: dict, repo: str) -> str | None:
    """Reach origin from the crew's transport repo, the one sf fetches and pushes through."""
    base = cfg["repos"][repo]["base"]
    out = gitsafe.git(transport.ensure(cfg, repo), "ls-remote", "--heads", "--upload-pack=git-upload-pack",
                      "origin", base, bare=True)
    return None if out else f"origin has no {base} branch"


def _crew_rows(cid: str, deps: dict, live: bool = False) -> list[Row]:
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
    live_cfg = {**cfg, "dryRun": False}  # dry run blocks every push; test the protected-branch rule itself
    rows.append(_guarded(f"{cid}: guard self-test", lambda: None if decide(
        "Bash", {"command": "git push origin main"}, str(paths.shopfloor_home()), live_cfg)
        else "git push origin main was allowed"))
    missing = [d for d in ("worktrees", "tickets") if not (paths.crew_dir(cid) / d).is_dir()]
    rows.append((not missing, f"{cid}: crew dirs", f"missing {', '.join(missing)} (run shopfloor install)" if missing else ""))
    if live:
        for name in cfg["repos"]:
            rows.append(_guarded(f"{cid}: git ls-remote {name}", lambda name=name: deps["lsremote"](cfg, name)))
        rows.append(_guarded(f"{cid}: live sandbox session", lambda: deps["smoke"](cid)))
        rows.append(_guarded(f"{cid}: board unreachable from the sandbox", lambda: deps["reach"](cid)))
    return rows


def check_all(crew_id: str | None, deps: dict | None = None, during_init: bool = False,
              live: bool = False) -> list[Row]:
    d = {"which": shutil.which, "run": proc.run, "tracker": make_tracker, "chat": make_chat, "health": _health,
         "smoke": live_smoke, "lsremote": ls_remote, "reach": board_reach}
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
    rows.append(_guarded("signing key", signing.key_problem))
    try:
        board = config.load_board()
        rows.append((True, "board config", ""))
        crews = [crew_id] if crew_id else config.list_crews()
    except config.ConfigError as e:
        return rows + [(False, "board config", redact(str(e)))]
    for cid in crews:
        rows += _crew_rows(cid, d, live)
    url = f"http://127.0.0.1:{board['port']}/healthz"
    up = d["health"](url)
    rows.append((up or during_init, "board", "" if up else f"not answering at {url}" + (" (warning)" if during_init else "")))
    return rows


def report(rows: list[Row], out=print) -> int:
    for ok, name, detail in rows:
        out(f"✓ {name}" if ok else f"✗ {name}: {detail}")
    return 0 if all(ok for ok, _, _ in rows) else 1
