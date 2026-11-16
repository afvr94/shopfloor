from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
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


def _rel(folder) -> tuple:
    try:
        return Path(os.path.abspath(folder)).relative_to(os.path.abspath(paths.shopfloor_home())).parts
    except ValueError:
        return ()


def is_locked(folder) -> bool:
    """<state>/<crew>, its ticketstate folder and each ticketstate/<KEY>."""
    parts = _rel(folder)
    return parts[:1] == ("state",) and (len(parts) == 2 or (len(parts) in (3, 4) and parts[2] == "ticketstate"))


def _entries(fd: int) -> dict:
    """name -> identity. A folder is known by its inode alone: its mtime moves whenever a crew writes inside
    tickets/ or another sf window opens below it."""
    out = {}
    with os.scandir(fd) as it:
        for e in it:
            st = e.stat(follow_symlinks=False)
            out[e.name] = (st.st_ino,) if stat.S_ISDIR(st.st_mode) else \
                (st.st_ino, st.st_size, st.st_mtime_ns, st.st_mode)
    return out


def _changed(before: dict, after: dict, declared: dict) -> list[str]:
    out = []
    for name in sorted(before.keys() | after.keys()):
        got = after.get(name)
        if before.get(name) == got:
            continue
        if name in declared:
            want = declared[name]
            if want is None or (got is not None and len(got) == 4 and got[:2] == want):
                continue
        out.append(name)
    return out


@contextlib.contextmanager
def unlocked(folder):
    """Write access to a locked folder for the length of the block; other folders pass through untouched.

    It yields a dict the caller fills with each name it changes: name -> (inode, size) the entry must end with,
    or None for any change. The folder's entries are compared before it opens and after it relocks; anything
    else that changed was written by someone else while the folder was open, and is flagged tampered."""
    key = os.path.abspath(folder)
    held_dirs = _held.__dict__.setdefault("dirs", {})
    if key in held_dirs:
        yield held_dirs[key]
        return
    if not is_locked(folder):
        yield {}
        return
    declared: dict = {}
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        before = _entries(fd)
        held_dirs[key] = declared
        os.fchmod(fd, OPEN_DIR)
        try:
            yield declared
        finally:
            os.fchmod(fd, LOCKED_DIR)
            held_dirs.pop(key, None)
            after = _entries(fd)
            os.close(fd)
            fd = -1
            _flag(folder, _changed(before, after, declared))
    finally:
        if fd >= 0:
            os.close(fd)


MARK = ".tampered"


def marker(path) -> Path:
    return Path(path).with_name(Path(path).name + MARK)


# The signed records, and the ticket folders that hold them; any other name changed in a window is reported only.
RECORDS = {"state.json", "briefing.json", "commands.jsonl", "commands.cursor", "lessons.json"}
_FLAG_ROUNDS, _FLAG_REPORTS = 50, 20


def _flag(folder, names: list[str]) -> None:
    """Report what changed behind sf's back, and leave a marker beside each record that did. Claude Code's Write
    and Edit can only create or replace a file, never delete one, so a marker outlives any later write and the
    record stays refused until the owner clears it. Writing a marker opens a window too; a writer still racing
    there queues more work here instead of recursing, and the rounds are capped."""
    parts = _rel(folder)
    if not names or parts[:1] != ("state",):
        return
    work = getattr(_held, "flagging", None)
    if work is not None:
        work.append((folder, names))
        return
    _held.flagging = work = [(folder, names)]
    try:
        for _ in range(_FLAG_ROUNDS):
            if not work:
                break
            folder, names = work.pop()
            parts = _rel(folder)
            for n, name in enumerate(names):
                path = Path(folder) / name
                key = parts[3] if len(parts) == 4 else name if len(parts) == 3 else None
                key = key if key and paths.TICKET_KEY_RE.fullmatch(key) else None
                with contextlib.suppress(OSError, ValueError):
                    if n < _FLAG_REPORTS:
                        report_tampered(parts[1], key, path, " (changed by another writer while sf had the folder open)")
                    elif n == _FLAG_REPORTS:
                        report_tampered(parts[1], None, folder, f" ({len(names) - n} more names changed)")
                    record = name in RECORDS or (len(parts) == 3 and key == name)
                    if record and not os.path.lexists(marker(path)):
                        atomic_write(marker(path), "changed by another writer while sf had the folder open\n")
    finally:
        _held.flagging = None


def read_record(path) -> bytes:
    """A signed record's bytes. Tampered if it or its folder carries a tamper marker, or it isn't a regular,
    non-link file you own with sf's mode for its folder (0400 in a locked one); FileNotFoundError passes through."""
    path = Path(path)
    for p in (path, path.parent):
        if os.path.lexists(marker(p)):
            raise signing.Tampered(f"record tampered: {p} was changed by another writer while sf had its folder "
                                   f"open; check it, then run `shopfloor doctor --repair-tamper <crew>`")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise signing.Tampered(f"record tampered: {path} can't be opened ({e.strerror})") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        want = _file_mode(path.parent)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != want:
            raise signing.Tampered(f"record tampered: {path} must be a regular file you own with mode {want:04o}")
        return f.read()


def locked_folders(crew_id: str) -> list[Path]:
    crew = paths.crew_dir(crew_id)
    root = crew / "ticketstate"
    found = [crew, root]
    with contextlib.suppress(OSError):
        found += sorted(root / n for n in os.listdir(root) if paths.TICKET_KEY_RE.fullmatch(n))
    return [p for p in found if p.is_dir() and not p.is_symlink()]


def repair_tamper(crew_id: str) -> list[Path]:
    """Remove a crew's tamper markers once the owner has checked the records; returns what it removed."""
    gone = []
    for folder in locked_folders(crew_id):
        for name in sorted(os.listdir(folder)):
            if name.endswith(MARK):
                remove(folder / name)
                gone.append(folder / name)
    return gone


def _sweep(folder: Path, fix: bool) -> list[str]:
    """What is open in locked folder `folder`; with `fix`, relock it under the folder's flock."""
    loose = []
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if fix:
            fcntl.flock(fd, fcntl.LOCK_EX)
        for name in sorted(os.listdir(fd)):
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) == LOCKED_FILE:
                continue
            loose.append(f"{folder / name} is {stat.S_IMODE(st.st_mode):04o}")
            if fix:
                with contextlib.suppress(OSError):
                    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    try:
                        os.fchmod(f, LOCKED_FILE)
                    finally:
                        os.close(f)
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
        if mode != LOCKED_DIR:
            loose.append(f"{folder} is {mode:04o}")
            if fix:
                os.fchmod(fd, LOCKED_DIR)
    finally:
        os.close(fd)
    return loose


def _sweep_all(crew_id: str, fix: bool) -> list[str]:
    return [x for f in locked_folders(crew_id) for x in _sweep(f, fix)]


def relock_all(crew_id: str) -> None:
    """Relock what a crash mid-window or a hand edit left open: every locked folder and file of the crew. Cheap
    enough to run at every start."""
    _sweep_all(crew_id, fix=True)


def open_locks(crew_id: str) -> list[str]:
    return _sweep_all(crew_id, fix=False)


def make_dir(folder) -> Path:
    """`folder` and any missing parents; a locked kind of folder is born locked."""
    folder = Path(folder)
    if not folder.is_dir():
        make_dir(folder.parent)
        with unlocked(folder.parent) as declared, contextlib.suppress(FileExistsError):
            declared[folder.name] = None
            os.mkdir(folder, LOCKED_DIR if is_locked(folder) else OPEN_DIR)
    return folder


def _file_mode(folder) -> int:
    return LOCKED_FILE if is_locked(folder) else 0o600


def atomic_write(path: Path, data: str) -> None:
    path = Path(path)
    folder = make_dir(path.parent)
    raw = data.encode()
    with unlocked(folder) as declared:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=path.name + ".")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
                os.fchmod(f.fileno(), _file_mode(folder))
                declared[path.name] = (os.fstat(f.fileno()).st_ino, len(raw))
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def append_line(path: Path, line: str) -> None:
    """Append `line`, starting it on a new line if the last write was torn."""
    path = Path(path)
    folder = make_dir(path.parent)
    with unlocked(folder) as declared:
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
                declared[path.name] = (b.st_ino, os.fstat(w).st_size)
            finally:
                os.close(w)
        finally:
            os.fchmod(fd, _file_mode(folder))
            os.close(fd)


def remove(path: Path) -> None:
    path = Path(path)
    with unlocked(path.parent) as declared, contextlib.suppress(FileNotFoundError):
        declared[path.name] = None
        os.unlink(path)


@contextlib.contextmanager
def held(path):
    """An exclusive flock on `path`, made on first use. It is opened read-only, so it works in a locked folder."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        folder = make_dir(path.parent)
        with unlocked(folder) as declared:
            declared[path.name] = None
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
            with unlocked(make_dir(path.parent)) as declared:
                declared[path.name] = None
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _file_mode(path.parent))
                with os.fdopen(fd, "w") as f:  # inside the window: a write after it would look foreign
                    f.write(body)
        except FileExistsError:
            if _fresh(_read_json(path), stale_minutes):
                return False
            atomic_write(path, body)
            return True
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
            text = read_record(path).decode()
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
