from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path

from . import signing, state

COMMANDS = ("approve", "adjust", "retry", "go", "pause", "resume", "cancel", "lesson", "forget")


class CommandQueueError(Exception):
    pass


def parse(text: str) -> dict:
    text = (text or "").strip()
    parts = text.split(None, 1)
    head, rest = (parts[0], parts[1] if len(parts) > 1 else "") if parts else ("", "")
    word = head[1:].lower() if head[:1] in ("/", "!") else head.lower()
    if word in COMMANDS:
        return {"cmd": word, "arg": rest.strip()}
    if text in tuple("123456789"):
        return {"cmd": "answer", "arg": text, "choice": int(text)}
    return {"cmd": "answer", "arg": text}


class CommandQueue:
    """Every line and the cursor carry sf's signature: whoever appends (sf, the board, the listener) runs outside
    the sandbox and signs, so a line a crew slipped in is reported and skipped, and a forged cursor stops the queue."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.crew = self.dir.name
        self.log = self.dir / "commands.jsonl"
        self.cursor = self.dir / "commands.cursor"
        self.lock = self.dir / "commands.lock"
        self.dir.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self):
        with open(self.lock, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def _all(self) -> list[dict]:
        try:
            lines = self.log.read_text().splitlines()
        except FileNotFoundError:
            return []
        rows = []
        for n, line in enumerate(lines, 1):
            try:
                raw = json.loads(line)
            except ValueError:  # torn line from a crash
                continue
            try:
                rows.append(signing.unseal(f"commands/{self.crew}", raw, f"{self.log} line {n}"))
            except signing.Tampered:
                state.report_tampered(self.crew, None, f"{self.log} line {n}")
        return [r for r in rows if isinstance(r.get("seq"), int)]

    def _acked(self) -> set[int]:
        try:
            raw = self.cursor.read_text()
        except FileNotFoundError:
            return set()
        try:
            return {int(n) for n in signing.unseal(f"commands.cursor/{self.crew}", json.loads(raw),
                                                   str(self.cursor))["acked"]}
        except signing.Tampered as e:
            state.report_tampered(self.crew, None, self.cursor)
            raise CommandQueueError(f"{e}; sf won't act on commands until you check it") from None
        except (ValueError, TypeError, KeyError):
            raise CommandQueueError(f"{self.cursor} is corrupt; fix or remove it") from None

    def append(self, key: str | None, source: str, text: str, ts: str) -> bool:
        with self._locked():
            rows = self._all()
            if any(r["source"] == source and r["ts"] == ts for r in rows):
                return False
            row = {"seq": max((r["seq"] for r in rows), default=0) + 1, "key": key, "source": source,
                   **parse(text), "text": text, "ts": ts}
            line = json.dumps(signing.seal(f"commands/{self.crew}", row)) + "\n"
            with open(self.log, "ab+") as f:
                f.seek(0, os.SEEK_END)
                if f.tell():
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        line = "\n" + line  # don't glue onto a torn line
                f.seek(0, os.SEEK_END)
                f.write(line.encode())
                f.flush()
                os.fsync(f.fileno())
        return True

    def pending(self) -> list[dict]:
        done = self._acked()
        return [r for r in self._all() if r["seq"] not in done]

    def ack(self, seq: int) -> None:
        """Acks seq and every command before it: commands apply in order, so acking the last one covers them."""
        with self._locked():
            acked = sorted(self._acked() | {r["seq"] for r in self._all() if r["seq"] <= seq} | {seq})
            state.atomic_write(self.cursor, json.dumps(signing.seal(f"commands.cursor/{self.crew}", {"acked": acked})))
