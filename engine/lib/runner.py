from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config, paths, state

HEARTBEAT_SECS = 60
KILL_GRACE_SECS = 10
_DROPPED_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
_NO_PROMPT_ENV = {"GIT_EDITOR": "true", "EDITOR": "true", "VISUAL": "true", "GIT_SEQUENCE_EDITOR": "true",
                  "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}


class RunnerBusy(Exception):
    pass


def _lock_path(crew_id: str):
    return paths.crew_state_dir(crew_id) / "build.lock"


def claude_invocation(crew_id: str, prompt: str) -> tuple[list[str], dict]:
    sandbox = {"sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {"allowWrite": [str(paths.worktrees_dir(crew_id)),
                                      str(paths.crew_state_dir(crew_id) / "tickets"),
                                      tempfile.gettempdir()]},
    }}
    argv = ["claude", "-p", prompt, "--plugin-dir", str(paths.ENGINE_DIR), "--permission-mode",
            "bypassPermissions", "--strict-mcp-config", "--settings", json.dumps(sandbox)]
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    env.update(_NO_PROMPT_ENV)
    env["SHOPFLOOR_CREW"] = crew_id
    env["SHOPFLOOR_HOME"] = str(paths.shopfloor_home())
    env["PATH"] = str(paths.SF_BIN) + os.pathsep + env.get("PATH", "")
    return argv, env


def status(crew_id: str) -> dict | None:
    return state.read_lock(_lock_path(crew_id))


def start(crew_id: str, keys: list[str]) -> int:
    if status(crew_id):
        raise RunnerBusy(f"a build run is already active for crew '{crew_id}'")
    proc = subprocess.Popen([sys.executable, "-m", "engine.lib.runner", crew_id, *keys],
                            start_new_session=True, cwd=paths.REPO_ROOT)
    return proc.pid


def _build_one(crew_id: str, key: str, max_minutes: float) -> None:
    argv, env = claude_invocation(crew_id, f"/shopfloor:build {key}")
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open(logs / f"build-{crew_id}-{key}-{ts}.log", "wb") as log:
        proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                cwd=paths.REPO_ROOT, start_new_session=True)
        try:
            code = proc.wait(timeout=max_minutes * 60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=KILL_GRACE_SECS)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            state.log_run(crew_id, key, "build timed out")
            return
    state.log_run(crew_id, key, "build finished" if code == 0 else f"build failed (exit {code})")


def run_builds(crew_id: str, keys: list[str]) -> None:
    limits = config.load_crew(crew_id)["limits"]
    lock = _lock_path(crew_id)
    if not state.acquire_lock(lock):
        raise RunnerBusy(f"a build run is already active for crew '{crew_id}'")
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(HEARTBEAT_SECS):
            state.beat(lock)

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        with ThreadPoolExecutor(max_workers=limits["maxParallel"]) as pool:
            for f in [pool.submit(_build_one, crew_id, k, limits["buildMaxMinutes"]) for k in keys]:
                f.result()
    finally:
        stop.set()
        state.release(lock)


if __name__ == "__main__":
    run_builds(sys.argv[1], sys.argv[2:])
