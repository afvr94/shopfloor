from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths, signing
from .redact import redact

STAGES = ("claimed", "planning", "needs_you", "waiting", "building", "in_review",
          "merged", "done", "stopped", "cancelled")
ACTIVE = STAGES[:6]

_FIELDS = ("title", "url", "thread", "prs", "branch", "question", "fixRounds", "reviewSeen")
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NOTE_MAX = 500


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


# sf's own folders that are not sandbox write roots are kept 0500, their files 0400, while idle. Claude Code's
# Write and Edit tools run outside the sandbox as the same user; with the folder read-only, the temp file they
# write next to the target and rename into place can't be made, whatever path a symlink race aims them at.
# sf opens a folder only for its own write, under a flock on the folder, and relocks it even if the write fails.
LOCKED_DIR, LOCKED_FILE, OPEN_DIR = 0o500, 0o400, 0o700
_held = threading.local()


def is_locked(folder) -> bool:
    """<state>/<crew>, its ticketstate folder and each ticketstate/<KEY>."""
    try:
        parts = Path(os.path.abspath(folder)).relative_to(os.path.abspath(paths.shopfloor_home())).parts
    except ValueError:
        return False
    return parts[:1] == ("state",) and (len(parts) == 2 or (len(parts) in (3, 4) and parts[2] == "ticketstate"))


@contextlib.contextmanager
def unlocked(folder):
    """Write access to a locked folder for the length of the block; other folders pass through untouched."""
    key = os.path.abspath(folder)
    held_dirs = _held.__dict__.setdefault("dirs", set())
    if key in held_dirs or not is_locked(folder):
        yield
        return
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held_dirs.add(key)
        os.fchmod(fd, OPEN_DIR)
        try:
            yield
        finally:
            os.fchmod(fd, LOCKED_DIR)
            held_dirs.discard(key)
    finally:
        os.close(fd)


def make_dir(folder) -> Path:
    """`folder` and any missing parents; a locked kind of folder is born locked."""
    folder = Path(folder)
    if not folder.is_dir():
        make_dir(folder.parent)
        with unlocked(folder.parent), contextlib.suppress(FileExistsError):
            os.mkdir(folder, LOCKED_DIR if is_locked(folder) else OPEN_DIR)
    return folder


def _file_mode(folder) -> int:
    return LOCKED_FILE if is_locked(folder) else 0o600


def atomic_write(path: Path, data: str) -> None:
    path = Path(path)
    folder = make_dir(path.parent)
    with unlocked(folder):
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=path.name + ".")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
                os.fchmod(f.fileno(), _file_mode(folder))
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def append_line(path: Path, line: str) -> None:
    """Append `line`, starting it on a new line if the last write was torn."""
    path = Path(path)
    folder = make_dir(path.parent)
    with unlocked(folder):
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            w = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            try:
                a, b = os.fstat(fd), os.fstat(w)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise OSError(f"{path} changed while being opened")
                if b.st_size and os.pread(fd, 1, b.st_size - 1) != b"\n":
                    line = "\n" + line
                data = line.encode()
                while data:
                    data = data[os.write(w, data):]
                os.fsync(w)
            finally:
                os.close(w)
        finally:
            os.fchmod(fd, _file_mode(folder))
            os.close(fd)


def remove(path: Path) -> None:
    path = Path(path)
    with unlocked(path.parent), contextlib.suppress(FileNotFoundError):
        os.unlink(path)


@contextlib.contextmanager
def held(path):
    """An exclusive flock on `path`, made on first use. It is opened read-only, so it works in a locked folder."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        folder = make_dir(path.parent)
        with unlocked(folder):
            fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, _file_mode(folder))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError, RecursionError):
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
    now = _now()
    body = json.dumps({"pid": os.getpid(), "startedAt": now, "heartbeat": now})
    # The guard serializes the stale check + replace so two processes can't both reclaim.
    with held(f"{path}.guard"):
        try:
            with unlocked(make_dir(path.parent)):
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _file_mode(path.parent))
        except FileExistsError:
            if _fresh(_read_json(path), stale_minutes):
                return False
            atomic_write(path, body)
            return True
        with os.fdopen(fd, "w") as f:
            f.write(body)
        return True


def beat(path: Path) -> None:
    path = Path(path)
    lock = _read_json(path)
    if lock and lock.get("pid") == os.getpid():
        lock["heartbeat"] = _now()
        atomic_write(path, json.dumps(lock))


def release(path: Path) -> None:
    path = Path(path)
    lock = _read_json(path)
    if lock and lock.get("pid") == os.getpid():
        remove(path)


def log_run(crew_id: str, key: str | None, result: str, note: str = "") -> None:
    line = json.dumps({"ts": _now(), "key": key, "result": result, "note": redact(note)[:NOTE_MAX]}) + "\n"
    append_line(paths.crew_state_dir(crew_id) / "runs.jsonl", line)


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
            "branch": "", "question": "", "fixRounds": 0, "reviewSeen": "", "updatedAt": "", "history": []}


_reported: set = set()


def report_tampered(crew_id: str, key: str | None, path, detail: str = "") -> None:
    """One run line per damaged file version per process: a board polling every few seconds doesn't flood the
    log, and a file that changes again is reported again."""
    try:
        st = os.stat(path)
        version = (st.st_mtime_ns, st.st_size)
    except OSError:
        version = None
    seen = (crew_id, str(path), version)
    if seen in _reported:
        return
    _reported.add(seen)
    with contextlib.suppress(OSError, ValueError):
        log_run(crew_id, key, "tampered", f"record tampered: {path}{detail}")


def _ticket_scope(crew_id: str, key: str) -> str:
    return f"ticket/{crew_id}/{key}"


def load_ticket(crew_id: str, key: str) -> dict:
    """The ticket's record. One that fails its signature check comes back as a needs_you ticket with no branch,
    PRs or thread, marked tampered, so nothing acts on it until the owner looks."""
    path = paths.ticket_state_dir(crew_id, key) / "state.json"
    if not os.path.lexists(path):
        return _blank(key)
    try:
        try:
            text = path.read_text()
        except (OSError, UnicodeError) as e:
            raise signing.Tampered(f"record tampered: {path} can't be read ({e})") from None
        stored = signing.read(_ticket_scope(crew_id, key), text, str(path))
    except signing.Tampered as e:
        report_tampered(crew_id, key, path)
        return {**_blank(key), "stage": "needs_you", "tampered": True,
                "question": f"{e}. sf won't act on {key} until you check it; delete the file to start the ticket over."}
    return {**_blank(key), **stored}


def save_ticket(crew_id: str, key: str, **changes) -> dict:
    unknown = set(changes) - set(_FIELDS) - {"stage"}
    if unknown:
        raise ValueError(f"unknown ticket fields: {', '.join(sorted(unknown))}")
    stage = changes.get("stage")
    if "stage" in changes and stage not in STAGES:
        raise ValueError(f"unknown stage: {stage!r}")
    path = paths.ticket_state_dir(crew_id, key) / "state.json"
    # Serialize read-modify-write so concurrent savers don't drop each other's fields.
    with held(make_dir(path.parent) / "state.lock"):
        t = load_ticket(crew_id, key)
        if t.get("tampered"):
            raise signing.Tampered(t["question"])
        now = _now()
        if "stage" in changes and stage != t["stage"]:
            t["history"].append({"ts": now, "stage": stage})
        t.update(changes)
        t["updatedAt"] = now
        atomic_write(path, json.dumps(signing.seal(_ticket_scope(crew_id, key), t), indent=2))
    return t


def list_tickets(crew_id: str) -> list[dict]:
    """All tickets for a crew, most recently updated first."""
    root = paths.ticket_state_root(crew_id)
    if not root.is_dir():
        return []
    tickets = [load_ticket(crew_id, d.name) for d in root.iterdir()
               if (d / "state.json").is_file() and paths.TICKET_KEY_RE.fullmatch(d.name)]
    return sorted(tickets, key=lambda t: t["updatedAt"], reverse=True)
