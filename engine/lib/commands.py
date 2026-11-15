from __future__ import annotations

import json
import os
from pathlib import Path

COMMANDS = ("approve", "adjust", "retry", "go", "pause", "resume", "cancel", "lesson", "forget")


def parse(text: str) -> dict:
    text = (text or "").strip()
    head, _, rest = text.partition(" ")
    word = head[1:].lower() if head[:1] in ("/", "!") else head.lower()
    if word in COMMANDS:
        return {"cmd": word, "arg": rest.strip()}
    if text in tuple("123456789"):
        return {"cmd": "answer", "arg": text, "choice": int(text)}
    return {"cmd": "answer", "arg": text}


class CommandQueue:
    def __init__(self, state_dir: Path):
        self.log = Path(state_dir) / "commands.jsonl"
        self.cursor = Path(state_dir) / "commands.cursor"
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def _all(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]

    def _acked(self) -> set[int]:
        try:
            return set(json.loads(self.cursor.read_text()))
        except (OSError, ValueError):
            return set()

    def append(self, key: str | None, source: str, text: str, ts: str) -> bool:
        rows = self._all()
        if any(r["source"] == source and r["ts"] == ts for r in rows):
            return False
        row = {"seq": max((r["seq"] for r in rows), default=0) + 1, "key": key, "source": source,
               **parse(text), "text": text, "ts": ts}
        with open(self.log, "a") as f:
            f.write(json.dumps(row) + "\n")
        return True

    def pending(self) -> list[dict]:
        done = self._acked()
        return [r for r in self._all() if r["seq"] not in done]

    def ack(self, seq: int) -> None:
        tmp = self.cursor.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self._acked() | {seq})))
        os.replace(tmp, self.cursor)
