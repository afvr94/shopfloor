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
from .redact import redact

HEARTBEAT_SECS = 60
KILL_GRACE_SECS = 10
_DROPPED_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
_NO_PROMPT_ENV = {"GIT_EDITOR": "true", "EDITOR": "true", "VISUAL": "true", "GIT_SEQUENCE_EDITOR": "true",
                  "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}


SF_EXCLUDED = ["sf *"]  # Bash rule syntax: sf with or without arguments
# The board listens on loopback and queues signed owner commands. In bypassPermissions mode the sandbox proxy lets
# a crew reach any host it hasn't been told to refuse, so loopback is refused by name; deniedDomains holds in every
# permission mode. allowLocalBinding stays off so a direct connection to localhost is blocked too (macOS).
LOOPBACK_DENIED = ["localhost", "*.localhost", "127.0.0.1", "0.0.0.0", "[::1]"]


class RunnerBusy(Exception):
    pass


def claude_invocation(crew_id: str, prompt: str) -> tuple[list[str], dict]:
    sandbox = {"sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        # sf writes crew state and pushes for ship/merge; it is the trusted mediator and the guard checks its args
        "excludedCommands": SF_EXCLUDED,
        "filesystem": {"allowWrite": [str(paths.worktrees_dir(crew_id)),
                                      str(paths.tickets_dir(crew_id)),
                                      tempfile.gettempdir()],
                       "denyRead": config.secret_paths(config.load_crew(crew_id))},
        "network": {"allowLocalBinding": False, "deniedDomains": LOOPBACK_DENIED},
    }}
    # --setting-sources "" loads none of the user's or a repo's settings, hooks or plugins; --settings still applies
    argv = ["claude", "-p", prompt, "--plugin-dir", str(paths.ENGINE_DIR), "--permission-mode",
            "bypassPermissions", "--strict-mcp-config", "--setting-sources", "", "--settings", json.dumps(sandbox)]
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    env.update(_NO_PROMPT_ENV)
    env["SHOPFLOOR_CREW"] = crew_id
    env["SHOPFLOOR_HOME"] = str(paths.shopfloor_home())
    env["PATH"] = str(paths.SF_BIN) + os.pathsep + env.get("PATH", "")
    return argv, env


def claude_cwd(crew_id: str):
    """Working dir for every crew claude session; the sandbox grants writes here, so never the repo root."""
    return state.make_dir(paths.worktrees_dir(crew_id))


def killpg(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def wait_or_kill(proc, timeout_s: float) -> int | None:
    """Exit code, or None after killing the whole process group (SIGTERM, then SIGKILL) on timeout."""
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=KILL_GRACE_SECS)
        except subprocess.TimeoutExpired:
            killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        return None


def start_heartbeat(lock) -> callable:
    """Keep `lock` fresh from a daemon thread; call the returned function to stop."""
    stop = threading.Event()

    def beat_loop():
        while not stop.wait(HEARTBEAT_SECS):
            state.beat(lock)

    threading.Thread(target=beat_loop, daemon=True).start()
    return stop.set


def status(crew_id: str) -> dict | None:
    return state.read_lock(paths.build_lock(crew_id))


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
        try:
            proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    cwd=claude_cwd(crew_id), start_new_session=True)
        except OSError as e:
            state.log_run(crew_id, key, f"build failed (could not start claude: {redact(str(e))})")
            return
        code = wait_or_kill(proc, max_minutes * 60)
        if code is None:
            state.log_run(crew_id, key, "build timed out")
            return
    state.log_run(crew_id, key, "build finished" if code == 0 else f"build failed (exit {code})")


def run_builds(crew_id: str, keys: list[str]) -> None:
    limits = config.load_crew(crew_id)["limits"]
    state.relock_all(crew_id)
    lock = paths.build_lock(crew_id)
    if not state.acquire_lock(lock):
        raise RunnerBusy(f"a build run is already active for crew '{crew_id}'")
    stop = start_heartbeat(lock)
    try:
        with ThreadPoolExecutor(max_workers=limits["maxParallel"]) as pool:
            for f in [pool.submit(_build_one, crew_id, k, limits["buildMaxMinutes"]) for k in keys]:
                f.result()
    finally:
        stop()
        state.release(lock)


if __name__ == "__main__":
    run_builds(sys.argv[1], sys.argv[2:])
