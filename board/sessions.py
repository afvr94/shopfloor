from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from engine.lib.redact import redact

WINDOW_HOURS = 24
RUNNING_QUIET_SECONDS = 90
LONG_TOOL_SECONDS = 45 * 60
FEED_LIMIT = 160
_KIND = re.compile(r"shopfloor:([\w-]+)(?:\s+([A-Za-z0-9][A-Za-z0-9-]{0,19}))?")
_CACHE: dict[Path, Session] = {}
_LOCK = threading.Lock()


def parse_ts(value: object) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() if value else None
    except ValueError:
        return None


def clip(text: object, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def short_path(p: object) -> str:
    parts = [x for x in str(p or "").split("/") if x]
    return "…/" + "/".join(parts[-2:]) if len(parts) > 2 else str(p or "a file")


def describe_tool(name: str, inp: object) -> tuple[str, str]:
    """(kind, sentence) for one tool call. Never includes file contents."""
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Read", "NotebookRead"):
        return "read", f"reading {short_path(inp.get('file_path') or inp.get('notebook_path'))}"
    if name in ("Grep", "Glob", "LS"):
        return "read", f"searching for {clip(inp.get('pattern') or inp.get('path') or 'files', 32)}"
    if name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return "edit", f"editing {short_path(inp.get('file_path') or inp.get('notebook_path'))}"
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        text = clip(inp.get("description"), 70) or f"running {clip(cmd, 60)}"
        if re.search(r"\b(test|pytest|unittest|lint|build|typecheck)\b", cmd.lower()):
            return "test", text
        return ("ship" if re.search(r"(^|[\s;&|(])(git|gh)\s", " " + cmd) else "run"), text
    if name in ("Agent", "Task"):
        return "helper", f"sending a helper: {clip(inp.get('description', 'a side task'), 50)}"
    if name in ("WebSearch", "WebFetch"):
        return "web", f"looking up {clip(inp.get('query') or inp.get('url') or 'the web', 40)}"
    if name.startswith("mcp__"):
        return "web", f"calling {clip(name.split('__', 1)[1].replace('__', ' '), 50)}"
    return "other", f"using {name}"


class Stream:
    """Incrementally reads one transcript file."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.partial = b""
        self.first_user_text: str | None = None
        self.first_ts: float | None = None

    def new_lines(self) -> list[dict]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset, self.partial = 0, b""
        if size == self.offset:
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read(size - self.offset)
        self.offset = size
        lines = (self.partial + chunk).split(b"\n")
        self.partial = lines.pop()
        out = []
        for raw in lines:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            self.first_ts = self.first_ts or parse_ts(obj.get("timestamp"))
            if self.first_user_text is None and obj.get("type") == "user":
                content = (obj.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if isinstance(content, str) and content:
                    self.first_user_text = content
            out.append(obj)
        return out


class Session:
    def __init__(self, path: Path):
        self.path = path
        self.stream = Stream(path)
        self.pending: dict[str, float] = {}
        self.helpers: dict[str, dict] = {}
        self.feed: list[dict] = []
        self.tool_count = 0
        self.errors = 0
        self.mtime = 0.0
        self.action = "clocking in"

    def kind(self) -> tuple[str, str | None]:
        m = _KIND.search(self.stream.first_user_text or "")
        return (m.group(1), m.group(2)) if m else ("session", None)

    def _push(self, ts: float, text: str, tone: str = "work") -> None:
        self.feed.append({"at": ts, "text": redact(text), "tone": tone})
        del self.feed[:-FEED_LIMIT]

    def _handle(self, obj: dict) -> None:
        ts = parse_ts(obj.get("timestamp")) or time.time()
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if obj.get("type") == "assistant" and block.get("type") == "tool_use":
                name = block.get("name", "")
                kind, text = describe_tool(name, block.get("input"))
                self.pending[block.get("id")] = ts
                self.tool_count += 1
                self.action = redact(text)
                self._push(ts, text)
                if kind == "helper":
                    self.helpers[block.get("id")] = {"label": clip((block.get("input") or {}).get("description"), 28),
                                                     "done": False}
            elif obj.get("type") == "assistant" and block.get("type") == "text" and block.get("text", "").strip():
                self.action = redact(clip(block["text"], 140))
                self._push(ts, self.action, "say")
            elif obj.get("type") == "user" and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                self.pending.pop(tid, None)
                if tid in self.helpers:
                    self.helpers[tid]["done"] = True
                if block.get("is_error"):
                    self.errors += 1
                    self._push(ts, "hit a snag", "error")

    def refresh(self) -> None:
        try:
            self.mtime = self.path.stat().st_mtime
        except OSError:
            return
        for obj in self.stream.new_lines():
            self._handle(obj)

    def running(self, now: float) -> bool:
        quiet = now - self.mtime
        if self.pending:
            return quiet < LONG_TOOL_SECONDS and now - min(self.pending.values()) < LONG_TOOL_SECONDS
        return quiet < RUNNING_QUIET_SECONDS

    def snapshot(self, now: float) -> dict:
        kind, arg = self.kind()
        running = self.running(now)
        return {"id": self.path.stem, "kind": kind, "arg": arg, "startedAt": self.stream.first_ts,
                "lastAt": self.mtime, "running": running, "actions": self.tool_count, "snags": self.errors,
                "action": self.action if running else "off shift",
                "helpersActive": sum(1 for h in self.helpers.values() if not h["done"]) if running else 0,
                "feed": self.feed[-40:]}


def _encode(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


def crew_sessions(projects_dir: Path, home: Path) -> list[dict]:
    with _LOCK:
        return _crew_sessions(projects_dir, home)


def _crew_sessions(projects_dir: Path, home: Path) -> list[dict]:
    """Snapshots of recent transcripts whose cwd lies inside <home>/state/<crew>/."""
    state_root = Path(home) / "state"
    if not Path(projects_dir).is_dir():
        return []
    prefixes = {_encode(state_root) + "-", _encode(state_root.resolve()) + "-"}
    crews = sorted((p.name for p in state_root.iterdir() if p.is_dir()), key=len, reverse=True) if state_root.is_dir() else []
    now = time.time()
    out, seen = [], set()
    for folder in Path(projects_dir).iterdir():
        rest = next((folder.name[len(x):] for x in prefixes if folder.name.startswith(x)), None)
        if rest is None or not folder.is_dir():
            continue
        crew = next((c for c in crews if rest == c or rest.startswith(c + "-")), None)
        if crew is None:
            continue
        for path in folder.glob("*.jsonl"):
            try:
                if now - path.stat().st_mtime > WINDOW_HOURS * 3600:
                    continue
            except OSError:
                continue
            seen.add(path)
            session = _CACHE.setdefault(path, Session(path))
            session.refresh()
            out.append({"crew": crew, **session.snapshot(now)})
    for gone in set(_CACHE) - seen:
        del _CACHE[gone]
    return sorted(out, key=lambda s: s["lastAt"], reverse=True)
