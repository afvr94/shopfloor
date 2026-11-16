"""A crew's lessons: short rules it learned from its own mistakes, read before every build, spec and fix.

The store is <crew state dir>/lessons.json. A lesson is one actionable sentence (what to do, and
when), never a story, never people's data or secrets. `add` bumps an existing lesson instead of
repeating it. At most MAX_ACTIVE are active: past that, the least-needed old one is retired (kept
in the file, not shown). The owner can forget or reword a lesson.
"""
from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import paths, signing, state
from .redact import find_sensitive

MAX_ACTIVE = 80
AREA = re.compile(r"[a-z0-9][a-z0-9_.-]{0,40}(/[a-z0-9][a-z0-9_.-]{0,40})?")
_SOURCE_MAX = 200


class LessonError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _file(crew_id: str) -> Path:
    return paths.crew_state_dir(crew_id) / "lessons.json"


def _scope(crew_id: str) -> str:
    return f"lessons/{crew_id}"


def _load(crew_id: str) -> tuple[Path, dict]:
    path = _file(crew_id)
    try:
        data = signing.read(_scope(crew_id), state.read_record(path).decode(), str(path))
    except FileNotFoundError:
        return path, {"next": 1, "lessons": []}
    except signing.Tampered as e:
        state.report_tampered(crew_id, None, path)
        raise LessonError(f"{e}; sf won't use lessons until you check it") from None
    except (OSError, ValueError) as e:
        raise LessonError(f"cannot read {path}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("lessons"), list) or not isinstance(data.get("next"), int):
        raise LessonError(f"{path} is not a lessons store")
    for l in data["lessons"]:
        if not (isinstance(l, dict) and isinstance(l.get("id"), int) and isinstance(l.get("area"), str)
                and isinstance(l.get("text"), str) and isinstance(l.get("state"), str)):
            raise LessonError(f"{path} has a malformed lesson entry")
    return path, data


@contextlib.contextmanager
def _locked(crew_id: str):
    with state.held(str(_file(crew_id)) + ".lock"):
        path, data = _load(crew_id)
        yield data
        state.atomic_write(path, json.dumps(signing.seal(_scope(crew_id), data), indent=1))


def _active(data: dict) -> list[dict]:
    return [l for l in data["lessons"] if l.get("state") == "active"]


def _find_active(data: dict, ident: object) -> dict:
    key = str(ident).strip().upper().lstrip("L")
    for l in _active(data):
        if str(l["id"]) == key:
            return l
    raise LessonError(f"no active lesson {ident}")


def _public(l: dict) -> dict:
    return {**l, "id": f"L{l['id']}"}


def _check_clean(*fields: str) -> None:
    for field in fields:
        kind = find_sensitive(field)
        if kind:
            raise LessonError(f"refused: it looks like it contains {kind}; write the rule without it")


def _check_text(text: str, source: str) -> str:
    text = " ".join(text.split())
    if not 15 <= len(text) <= 240:
        raise LessonError("a lesson is one sentence of 15-240 characters")
    _check_clean(text, source)
    return text


def _bump(l: dict, source: str) -> None:
    l["count"] = l.get("count", 1) + 1
    l["last"] = _now()
    l["sources"] = (l.get("sources") or [])[-4:] + [source[:_SOURCE_MAX]]


def show(crew_id: str, area: str | None = None) -> list[dict]:
    _, data = _load(crew_id)
    prefix = (area or "").strip().lower()
    rows = [l for l in _active(data)
            if not prefix or l["area"] in (prefix, "all")
            or l["area"].startswith(prefix + "/") or prefix.startswith(l["area"] + "/")]
    rows.sort(key=lambda l: (-l.get("count", 1), l["id"]))
    return [_public(l) for l in rows]


def add(crew_id: str, area: str, text: str, source: str) -> dict:
    area = area.strip().lower()
    if not (AREA.fullmatch(area) or area == "all"):
        raise LessonError("area must look like `repo` or `repo/area` (lowercase), or `all`")
    text = _check_text(text, source)
    with _locked(crew_id) as data:
        same = next((l for l in _active(data) if l["area"] == area and _norm(l["text"]) == _norm(text)), None)
        if same:
            _bump(same, source)
            return _public(same)
        stamp = _now()
        lesson = {"id": data["next"], "area": area, "text": text, "count": 1, "state": "active",
                  "added": stamp, "last": stamp, "sources": [source[:_SOURCE_MAX]]}
        data["next"] += 1
        data["lessons"].append(lesson)
        live = _active(data)
        if len(live) > MAX_ACTIVE:
            old = min(live, key=lambda l: (l.get("count", 1), l.get("last", l["added"])))
            old["state"], old["ended"] = "retired", stamp
        return _public(lesson)


def bump(crew_id: str, lesson_id: str, source: str) -> dict:
    _check_clean(source)
    with _locked(crew_id) as data:
        l = _find_active(data, lesson_id)
        _bump(l, source)
        return _public(l)


def forget(crew_id: str, lesson_id: str) -> dict:
    with _locked(crew_id) as data:
        l = _find_active(data, lesson_id)
        l["state"], l["ended"] = "forgotten", _now()
        return _public(l)


def edit(crew_id: str, lesson_id: str, text: str, source: str) -> dict:
    text = _check_text(text, source)
    with _locked(crew_id) as data:
        l = _find_active(data, lesson_id)
        l["text"] = text
        l["last"] = _now()
        l["sources"] = (l.get("sources") or [])[-4:] + [source[:_SOURCE_MAX]]
        return _public(l)
