from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths
from .redact import redact

STAGES = ("claimed", "planning", "needs_you", "waiting", "building", "in_review",
          "merged", "done", "stopped", "cancelled")
ACTIVE = STAGES[:6]

_FIELDS = ("title", "url", "thread", "prs", "branch", "question", "fixRounds")
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NOTE_MAX = 500


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _fresh(lock: dict | None, stale_minutes: int) -> bool:
    if not lock or not _pid_alive(lock.get("pid")):
        return False
    try:
        beat = datetime.strptime(lock["heartbeat"], _TS_FORMAT).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - beat <= timedelta(minutes=stale_minutes)


def read_lock(path: Path, stale_minutes: int = 45) -> dict | None:
    lock = _read_json(Path(path))
    return lock if _fresh(lock, stale_minutes) else None


def acquire_lock(path: Path, stale_minutes: int = 45) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now()
    body = json.dumps({"pid": os.getpid(), "startedAt": now, "heartbeat": now})
    # The guard serializes the stale check + replace so two processes can't both reclaim.
    with open(f"{path}.guard", "a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _fresh(_read_json(path), stale_minutes):
                return False
            _atomic_write(path, body)
            return True
        with os.fdopen(fd, "w") as f:
            f.write(body)
        return True


def beat(path: Path) -> None:
    path = Path(path)
    lock = _read_json(path)
    if lock and lock.get("pid") == os.getpid():
        lock["heartbeat"] = _now()
        _atomic_write(path, json.dumps(lock))


def release(path: Path) -> None:
    path = Path(path)
    lock = _read_json(path)
    if lock and lock.get("pid") == os.getpid():
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def log_run(crew_id: str, key: str | None, result: str, note: str = "") -> None:
    line = json.dumps({"ts": _now(), "key": key, "result": result, "note": redact(note)[:NOTE_MAX]}) + "\n"
    with open(paths.crew_state_dir(crew_id) / "runs.jsonl", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)


def recent_runs(crew_id: str, keep: int = 60) -> list[dict]:
    try:
        lines = (paths.crew_state_dir(crew_id) / "runs.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return []
    runs = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            runs.append(d)
    return runs[-keep:] if keep > 0 else []


def _blank(key: str) -> dict:
    return {"key": key, "title": "", "url": "", "stage": None, "thread": None, "prs": [],
            "branch": "", "question": "", "fixRounds": 0, "updatedAt": "", "history": []}


def load_ticket(crew_id: str, key: str) -> dict:
    stored = _read_json(paths.ticket_dir(crew_id, key) / "state.json") or {}
    return {**_blank(key), **stored}


def save_ticket(crew_id: str, key: str, **changes) -> dict:
    unknown = set(changes) - set(_FIELDS) - {"stage"}
    if unknown:
        raise ValueError(f"unknown ticket fields: {', '.join(sorted(unknown))}")
    stage = changes.get("stage")
    if "stage" in changes and stage not in STAGES:
        raise ValueError(f"unknown stage: {stage!r}")
    path = paths.ticket_dir(crew_id, key) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize read-modify-write so concurrent savers don't drop each other's fields.
    with open(path.parent / "state.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        t = load_ticket(crew_id, key)
        now = _now()
        if "stage" in changes and stage != t["stage"]:
            t["history"].append({"ts": now, "stage": stage})
        t.update(changes)
        t["updatedAt"] = now
        _atomic_write(path, json.dumps(t, indent=2))
    return t


def list_tickets(crew_id: str) -> list[dict]:
    """All tickets for a crew, most recently updated first."""
    root = paths.crew_state_dir(crew_id) / "tickets"
    if not root.is_dir():
        return []
    tickets = [load_ticket(crew_id, d.name) for d in root.iterdir()
               if (d / "state.json").is_file() and paths.TICKET_KEY_RE.fullmatch(d.name)]
    return sorted(tickets, key=lambda t: t["updatedAt"], reverse=True)
