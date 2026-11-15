from __future__ import annotations

from dataclasses import dataclass


class ChatError(Exception):
    pass


@dataclass(frozen=True)
class ThreadRef:
    channel: str
    ts: str

    def to_dict(self) -> dict:
        return {"channel": self.channel, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict) -> ThreadRef:
        return cls(d["channel"], d["ts"])


@dataclass(frozen=True)
class Reply:
    key: str
    user: str
    text: str
    ts: str
