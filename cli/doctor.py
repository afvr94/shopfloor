from __future__ import annotations

import http.server
import json
import os
import re
import secrets
import shutil
import stat
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
CLAUDE_MIN = (2, 1, 284)  # the first with the sandbox proxy's local-address check
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


def _claude_version() -> str:
    return proc.run(["claude", "--version"], timeout=30)


def version_problem(out: str) -> str | None:
    want = ".".join(map(str, CLAUDE_MIN))
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    if not m:
        return f"can't read a version from `claude --version`; Shopfloor needs {want} or newer"
    if tuple(map(int, m.groups())) < CLAUDE_MIN:
        return (f"claude {m.group(0)} is older than {want}, which added the sandbox's local-address check crews "
                f"rely on to keep off the board; upgrade with: claude update")
    return None


def _secret_row(label: str, path: str) -> Row:
    """0400 as sf writes it passes; 0600, a file you wrote by hand, passes with a warning; anything looser fails."""
    try:
        mode = stat.S_IMODE(os.stat(os.path.expanduser(path)).st_mode)
    except OSError:
        return (False, f"{label} secret", f"{path} is missing or unreadable")
    if mode & 0o077:
        return (False, f"{label} secret", f"{path} must be mode 0400 or 0600 (chmod 400 it)")
    return (True, f"{label} secret", "" if mode == 0o400 else f"warning: {path} is {mode:04o}; chmod 400 it")


def _secrets_dir_problem() -> str | None:
    d = state.secrets_dir()
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) != state.LOCKED_DIR:
        return f"{d} must be a folder with mode 0500 (chmod 500 it; any shopfloor run or sf call relocks it)"
    return None


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
                info = json.loads(out)
                if info.get("name") != branch:  # GitHub answers a missing master with the default branch
                    return None
                if not info.get("protected"):
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


REACH_SCRIPT = """import os, socket

PORT = %d
PROXY = os.environ.get("HTTP_PROXY", "")


def ask(host, port, request):
    c = socket.create_connection((host, port), 5)
    try:
        c.sendall(request)
        c.recv(4096)
    finally:
        c.close()


def get(host):
    return f"GET http://{host}:{PORT}/healthz HTTP/1.1\\r\\nHost: {host}:{PORT}\\r\\n\\r\\n".encode()


def proxy():
    host, port = PROXY.split("//")[-1].strip("/").rsplit(":", 1)
    return host, int(port)


def connect_decimal():
    target = f"2130706433:{PORT}"
    c = socket.create_connection(proxy(), 5)
    try:
        c.sendall(f"CONNECT {target} HTTP/1.1\\r\\nHost: {target}\\r\\n\\r\\n".encode())
        c.recv(4096)
        c.sendall(b"GET /healthz HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n")
        c.recv(4096)
    finally:
        c.close()


tries = [lambda h=h: ask(h, PORT, get(h)) for h in ("127.0.0.1", "localhost")]
tries += [lambda h=h: ask(*proxy(), get(h)) for h in ("127.0.0.1", "[::ffff:127.0.0.1]", "[::ffff:7f00:1]")]
tries.append(connect_decimal)
with open("reach.txt", "a") as out:
    out.write(f"proxy={PROXY}\\n")
    for attempt in tries:
        try:
            attempt()
            rc = 0
        except Exception:
            rc = 1
        out.write(f"rc={rc}\\n")
"""
REACH_ATTEMPTS = 6


def _reach_probe(port: int) -> tuple[str, int]:
    """The script a session runs, and how many attempts it makes. It records $HTTP_PROXY, then tries the stand-in
    directly, through the proxy by name and by IPv4-mapped IPv6 spellings, and with a raw CONNECT to the decimal
    form of 127.0.0.1, which a client's URL parser would otherwise normalize away. It is a file in the crew's
    worktrees, the only place the guard lets a crew run a program from, so the check reaches the sandbox."""
    return REACH_SCRIPT % port, REACH_ATTEMPTS


def board_reach(cid: str) -> str | None:
    """A real sandboxed crew session tries to reach a stand-in for the board on 127.0.0.1 (see _reach_probe). It
    passes only if the session ran every attempt, had a proxy to go through, and nothing reached the stand-in."""
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
    probe, attempts = _reach_probe(port)
    argv, env = runner.claude_invocation(cid, (
        "This is a Shopfloor doctor check of the sandbox's network rules. Use the Bash tool once to run this "
        "exactly as written, then reply DONE: `python3 reach.py`"))
    try:
        with tempfile.TemporaryDirectory(prefix=".doctor-reach-", dir=paths.worktrees_dir(cid)) as d:
            with open(os.path.join(d, "reach.py"), "x") as f:
                f.write(probe)
            try:
                r = subprocess.run(argv, env=env, cwd=d, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   timeout=SMOKE_SECS)
            except subprocess.TimeoutExpired:
                return f"claude did not finish within {SMOKE_SECS}s"
            try:
                with open(os.path.join(d, "reach.txt")) as f:
                    lines = f.read().splitlines()
            except OSError:
                lines = []
    finally:
        server.shutdown()
        server.server_close()
    if hits:
        return f"a sandboxed command reached 127.0.0.1:{port} ({len(hits)} requests): crews could reach the board"
    ran = [x for x in lines if x.startswith("rc=")]
    if len(ran) != attempts:
        return f"the session did not run the probe (claude exited {r.returncode}); can't tell"
    proxy = next((x[len("proxy="):] for x in lines if x.startswith("proxy=")), "")
    if not proxy.strip():
        return "the session had no $HTTP_PROXY, so the proxy route was never tried; can't tell"
    return None


def ls_remote(cfg: dict, repo: str) -> str | None:
    """Reach origin from the crew's transport repo, the one sf fetches and pushes through."""
    base = cfg["repos"][repo]["base"]
    out = gitsafe.git(transport.ensure(cfg, repo), "ls-remote", "--heads", "--upload-pack=git-upload-pack",
                      "origin", base, bare=True)
    return None if out else f"origin has no {base} branch"


def _locks_problem(cid: str) -> str | None:
    loose = state.open_locks(cid)
    if loose:
        return (f"{len(loose)} open while idle, e.g. {loose[0]} (a crash mid-write or a hand edit; "
                f"the next shopfloor run, sf call or board start relocks them)")
    return None


def _markers_problem(cid: str) -> str | None:
    marks = [f / n for f in state.locked_folders(cid) for n in sorted(os.listdir(f)) if n.endswith(state.MARK)]
    if marks:
        return (f"{len(marks)} record(s) changed by another writer while sf had their folder open, e.g. "
                f"{marks[0]}; check them, then run: shopfloor doctor --repair-tamper {cid}")
    return None


def _crew_rows(cid: str, deps: dict, live: bool = False) -> list[Row]:
    try:
        cfg = config.load_crew(cid)
    except config.ConfigError as e:
        return [(False, f"{cid}: config", redact(str(e)))]
    rows: list[Row] = [(True, f"{cid}: config", "")]
    ttype, ctype = cfg["tracker"]["type"], cfg["chat"]["type"]
    rows += [_secret_row(f"{cid}: {k}", cfg[k]["tokenFile"]) for k in ("tracker", "chat") if cfg[k]["type"] in ("linear", "slack")]
    tracker, chat = deps["tracker"](cfg), deps["chat"](cfg)
    rows.append(_guarded(f"{cid}: {ttype} viewer", lambda: tracker.viewer() and None))
    rows.append(_guarded(f"{cid}: {ttype} team", lambda: "; ".join(tracker.team_check()) or None))
    rows.append(_guarded(f"{cid}: {ctype} auth", lambda: chat.auth_test() and None))
    rows.append(_guarded(f"{cid}: {ctype} channel", chat.channel_check))
    rows += _repo_rows(cid, cfg, deps)
    live_cfg = {**cfg, "dryRun": False}  # dry run blocks every push; test the protected-branch rule itself
    rows.append(_guarded(f"{cid}: guard self-test", lambda: None if decide(
        "Bash", {"command": "git push origin main"}, str(paths.shopfloor_home()), live_cfg)
        else "git push origin main was allowed"))
    rows.append(_guarded(f"{cid}: locked folders", lambda: _locks_problem(cid)))
    rows.append(_guarded(f"{cid}: tamper markers", lambda: _markers_problem(cid)))
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
         "smoke": live_smoke, "lsremote": ls_remote, "reach": board_reach, "version": _claude_version}
    d.update(deps or {})
    which = d["which"]
    rows: list[Row] = [
        (sys.platform in ("darwin", "linux"), "platform", sys.platform),
        (sys.version_info >= (3, 10), "python", "needs 3.10 or newer" if sys.version_info < (3, 10) else ""),
    ]
    rows += [(bool(which(t)), t, "" if which(t) else f"{t} not found on PATH") for t in ("claude", "git", "gh")]
    if which("claude"):
        rows.append(_guarded("claude version", lambda: version_problem(d["version"]())))
    rows.append(_guarded("gh auth", lambda: d["run"](["gh", "auth", "status"]) and None))
    tools, hint = SANDBOX_HINT.get(sys.platform, ([], ""))
    absent = [t for t in tools if not which(t)]
    rows.append((not absent, "sandbox", f"{', '.join(absent)} missing: {hint}" if absent else ""))
    rows.append(_guarded("signing key", signing.key_problem))
    rows.append(_guarded("secrets folder", _secrets_dir_problem))
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
        out((f"✓ {name}: {detail}" if detail else f"✓ {name}") if ok else f"✗ {name}: {detail}")
    return 0 if all(ok for ok, _, _ in rows) else 1
