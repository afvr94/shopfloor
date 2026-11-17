from __future__ import annotations

import json
import re

from engine.adapters.chat.base import ChatError, Reply, ThreadRef
from engine.adapters.tracker.base import Ticket, settings
from engine.adapters.tracker.github import MARKER
from engine.lib import proc
from engine.lib.config import sign
from engine.lib.redact import redact


class GitHubChat:
    def __init__(self, cfg: dict, run=None):
        self.owner = cfg["chat"]["github"]["owner"]
        self.repo = settings(cfg)["repo"]
        self.voice = cfg["voice"]["name"]
        self.run = run or (lambda argv, input=None: proc.run(argv, input=input))

    def _gh(self, *args: str, input: str | None = None) -> str:
        try:
            return self.run(["gh", *args], input=input)
        except proc.ProcError as e:
            if "rate limit" in e.stderr.lower():
                raise ChatError("GitHub API rate limit reached; the next run tries again: " + redact(e.stderr)) from None
            raise ChatError(redact(str(e))) from None

    def _comments_path(self, channel: str) -> str:
        # The crew can't set the thread, but a stale or hand-edited one must still never reach another repo.
        m = re.fullmatch(re.escape(self.repo) + r"#([0-9]+)", channel, re.IGNORECASE)
        if not m:
            raise ChatError(f"thread {channel!r} is not an issue in {self.repo}; this ticket's thread was opened "
                            "elsewhere — cancel the ticket or start it over")
        return f"repos/{self.repo}/issues/{m.group(1)}/comments"

    def open_thread(self, ticket: Ticket) -> ThreadRef:
        n = ticket.key.split("-", 1)[1]
        channel = f"{self.repo}#{n}"
        ids = [int(i) for i in self._gh("api", "--paginate", self._comments_path(channel), "--jq", ".[].id").split()]
        return ThreadRef(channel, str(max(ids, default=0)))

    def post(self, thread: ThreadRef, text: str) -> str:
        out = self._gh("api", "-X", "POST", self._comments_path(thread.channel), "--input", "-", "--jq", ".id",
                       input=json.dumps({"body": sign(text, self.voice) + "\n\n" + MARKER}))
        return out.strip()

    def ask(self, thread: ThreadRef, question: str, options: list[str], recommendation: int) -> str:
        lines = [question] + [f"{i}. {o}" for i, o in enumerate(options, 1)]
        lines.append(f"Recommended: {recommendation}. Reply with a number, or write your own answer.")
        return self.post(thread, "\n".join(lines))

    def poll(self, threads: dict[str, ThreadRef], since: dict[str, str]) -> list[Reply]:
        out: list[Reply] = []
        for key, th in threads.items():
            path = self._comments_path(th.channel)
            floor = since.get(key, th.ts)
            if not re.fullmatch(r"[0-9]+", floor):
                raise ChatError(f"{key}'s reply cursor {floor!r} isn't a GitHub comment id; the thread was opened "
                                "elsewhere — cancel the ticket or start it over")
            floor = int(floor)
            raw = self._gh("api", "--paginate", path, "--jq", ".[]")
            for c in map(json.loads, filter(None, raw.splitlines())):
                body = c.get("body") or ""
                if (c["id"] > floor and c["user"]["login"].lower() == self.owner.lower()
                        and not body.rstrip().endswith(MARKER)):
                    out.append(Reply(key, self.owner, body, str(c["id"])))
        return sorted(out, key=lambda r: int(r.ts))

    def auth_test(self) -> str:
        return self._gh("api", "user", "--jq", ".login").strip()

    def channel_check(self) -> str | None:
        if not json.loads(self._gh("api", f"repos/{self.repo}")).get("has_issues"):
            return f"issues are turned off in {self.repo}"
        return None
