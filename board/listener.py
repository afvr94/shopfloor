from __future__ import annotations

import json
import sys
import threading

from engine.adapters.chat import make_chat
from engine.adapters.chat.base import ThreadRef
from engine.lib import config, paths, state
from engine.lib.commands import CommandQueue
from engine.lib.redact import redact

CURSOR_FILE = "listener.json"


def _read_cursors(path) -> dict:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}


def _poll_crew(crew_id: str) -> int:
    sdir = paths.crew_dir(crew_id)
    if not sdir.is_dir():
        return 0
    threads = {t["key"]: ThreadRef.from_dict(t["thread"]) for t in state.list_tickets(crew_id)
               if t["stage"] in state.ACTIVE and t["thread"]}
    if not threads:
        return 0
    cursor_path = sdir / CURSOR_FILE
    since = _read_cursors(cursor_path)
    queue = CommandQueue(sdir)
    queued = 0
    try:
        cfg = config.load_crew(crew_id)
        for r in make_chat(cfg).poll(threads, since):
            queued += queue.append(r.key, cfg["chat"]["type"], r.text, r.ts)
            since[r.key] = r.ts
    finally:
        if since != _read_cursors(cursor_path):
            state.atomic_write(cursor_path, json.dumps(since))
    return queued


def poll_once() -> int:
    total = 0
    for crew_id in config.list_crews():
        try:
            total += _poll_crew(crew_id)
        except Exception as e:  # one crew's failure must not stop the others
            print(f"listener: crew {crew_id}: {redact(str(e))}", file=sys.stderr, flush=True)
    return total


def run_forever(stop: threading.Event, interval: float) -> None:
    while not stop.is_set():
        try:
            poll_once()
        except Exception as e:
            print(f"listener: {redact(str(e))}", file=sys.stderr, flush=True)
        stop.wait(interval)
