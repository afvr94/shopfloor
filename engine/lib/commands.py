from __future__ import annotations

import json
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
    """A signed, chained log: each line's signature covers its seq and the previous line's signature, and the
    signed cursor names the last acked line by seq and signature. Whoever appends (sf, the board, the listener)
    runs outside the sandbox and signs. A line dropped, duplicated, reordered or cut from the end, or a forged
    line or cursor, makes the whole queue refuse until the owner checks it."""

    def __init__(self, state_dir: Path):
        self.dir = state.make_dir(state_dir)
        self.crew = self.dir.name
        self.log = self.dir / "commands.jsonl"
        self.cursor = self.dir / "commands.cursor"
        self.lock = self.dir / "commands.lock"

    def _tampered(self, path, why: str) -> CommandQueueError:
        state.report_tampered(self.crew, None, path, f" ({why})")
        return CommandQueueError(f"record tampered: {path} ({why}); sf won't act on commands until you check it")

    def _chain(self) -> list[tuple[dict, str]]:
        """(row, signature) for every line, checked link by link."""
        try:
            lines = self.log.read_bytes().splitlines()
        except FileNotFoundError:
            return []
        out: list[tuple[dict, str]] = []
        for n, line in enumerate(lines, 1):
            try:
                raw = json.loads(line)
            except (ValueError, RecursionError):  # torn line from a crash: the next line links past it
                continue
            try:
                row = signing.unseal(f"commands/{self.crew}", raw, f"{self.log} line {n}")
            except signing.Tampered:
                raise self._tampered(self.log, f"line {n} fails its signature check") from None
            seq, prev = (out[-1][0]["seq"], out[-1][1]) if out else (0, "")
            if row.get("seq") != seq + 1 or row.get("prev") != prev:
                raise self._tampered(self.log, f"line {n} is out of order: a line was dropped, repeated or moved")
            out.append((row, raw[signing.FIELD]))
        return out

    def _acked(self, chain: list[tuple[dict, str]]) -> int:
        """The highest acked seq, checked against the log."""
        try:
            raw = self.cursor.read_text()
        except FileNotFoundError:
            return 0
        except (OSError, UnicodeError):
            raise self._tampered(self.cursor, "unreadable") from None
        try:
            cur = signing.read(f"commands.cursor/{self.crew}", raw, str(self.cursor))
        except signing.Tampered:
            raise self._tampered(self.cursor, "fails its signature check") from None
        high, last = cur.get("high"), cur.get("last")
        if not isinstance(high, int) or isinstance(high, bool) or high < 0:
            raise self._tampered(self.cursor, "has no acked seq")
        if high and (high > len(chain) or chain[high - 1][1] != last):
            raise self._tampered(self.log, f"acked line {high} is missing or changed: the log was cut or rewritten")
        return high

    @staticmethod
    def _public(row: dict) -> dict:
        return {k: v for k, v in row.items() if k != "prev"}

    def append(self, key: str | None, source: str, text: str, ts: str) -> bool:
        with state.held(self.lock):
            chain = self._chain()
            high = self._acked(chain)
            if any(r.get("source") == source and r.get("ts") == ts for r, _ in chain):
                return False
            row = {"seq": max(len(chain), high) + 1, "prev": chain[-1][1] if chain else "", "key": key,
                   "source": source, **parse(text), "text": text, "ts": ts}
            state.append_line(self.log, json.dumps(signing.seal(f"commands/{self.crew}", row)) + "\n")
        return True

    def pending(self) -> list[dict]:
        chain = self._chain()
        high = self._acked(chain)
        return [self._public(r) for r, _ in chain[high:]]

    def ack(self, seq: int) -> None:
        """Acks seq and every command before it: commands apply in order, so acking the last one covers them."""
        with state.held(self.lock):
            chain = self._chain()
            high = self._acked(chain)
            if not 0 < seq <= len(chain):
                raise CommandQueueError(f"no command {seq} in the queue")
            if seq > high:
                cur = {"high": seq, "last": chain[seq - 1][1]}
                state.atomic_write(self.cursor, json.dumps(signing.seal(f"commands.cursor/{self.crew}", cur)))
