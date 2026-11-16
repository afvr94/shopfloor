from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone

from board.server import serve
from board.state import crew_state
from cli import doctor, init, install
from engine.lib import config, paths, runner, state
from engine.lib.redact import redact


def run_tick(crew_id: str, popen=subprocess.Popen, timeout: float | None = None) -> int:
    cfg = config.load_crew(crew_id)
    lock = paths.tick_lock(crew_id)
    if not state.acquire_lock(lock):
        state.log_run(crew_id, None, "skipped: locked")
        return 0
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(runner.HEARTBEAT_SECS):
            state.beat(lock)

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        argv, env = runner.claude_invocation(crew_id, "/shopfloor:tick")
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with open(paths.logs_dir() / f"tick-{crew_id}-{ts}.log", "wb") as log:
            try:
                proc = popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             cwd=runner.claude_cwd(crew_id), start_new_session=True)
            except OSError as e:
                state.log_run(crew_id, None, f"tick failed (could not start claude: {redact(str(e))})")
                return 1
            try:
                code = proc.wait(timeout=timeout if timeout is not None else cfg["limits"]["tickMaxMinutes"] * 60)
            except subprocess.TimeoutExpired:
                runner._killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=runner.KILL_GRACE_SECS)
                except subprocess.TimeoutExpired:
                    runner._killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                state.log_run(crew_id, None, "tick timed out")
                return 1
        state.log_run(crew_id, None, "tick finished" if code == 0 else f"tick failed (exit {code})")
        return 0 if code == 0 else 1
    finally:
        stop.set()
        state.release(lock)


def status_line(crew_id: str) -> str:
    c = crew_state(crew_id)
    if c.get("error"):
        return f"{crew_id}: error: {' '.join(c['error'].split())}"
    counts: dict[str, int] = {}
    for t in c["tickets"]:
        counts[t["stage"]] = counts.get(t["stage"], 0) + 1
    activity = "building" if c["building"] else "running" if c["running"] else "idle"
    flags = ["dry run" if c["dryRun"] else "live"] + (["paused"] if c["paused"] else [])
    stages = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no tickets"
    return f"{crew_id}: {stages} | {activity} | next {c['nextRun'] or 'n/a'} | {', '.join(flags)}"


def _install(crew: str | None) -> int:
    rc = 0
    install.install_board(config.load_board())
    for cid in [crew] if crew else config.list_crews():
        try:
            cfg = config.load_crew(cid)
        except config.ConfigError as e:
            print(redact(str(e)), file=sys.stderr)
            rc = 1
            continue
        init.ensure_dirs(cid)
        if "schedule" not in cfg:
            print(f"{cid}: no schedule in its config; not installed", file=sys.stderr)
            continue
        install.install_crew(cfg)
        print(f"installed {cid}")
    return rc


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shopfloor")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("doctor", "install", "status"):
        sub.add_parser(name).add_argument("crew", nargs="?")
    sub.add_parser("run").add_argument("crew")
    for name in ("init", "uninstall", "board"):
        sub.add_parser(name)
    return p


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    try:
        if args.cmd == "init":
            return init.run_wizard()
        if args.cmd == "doctor":
            return doctor.report(doctor.check_all(args.crew))
        if args.cmd == "install":
            return _install(args.crew)
        if args.cmd == "uninstall":
            for p in install.uninstall():
                print(f"removed {p}")
            return 0
        if args.cmd == "run":
            return run_tick(args.crew)
        if args.cmd == "board":
            serve(config.load_board())
            return 0
        for cid in [args.crew] if args.crew else config.list_crews():
            print(status_line(cid))
        return 0
    except (config.ConfigError, ValueError) as e:
        print(redact(str(e)), file=sys.stderr)
        return 1
