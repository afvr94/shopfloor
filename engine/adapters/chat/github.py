from __future__ import annotations

import json

from engine.adapters.chat.base import ChatError, Reply, ThreadRef
from engine.adapters.tracker.base import Ticket, settings
from engine.adapters.tracker.github import MARKER
from engine.lib import proc
from engine.lib.redact import redact


class GitHubChat:
    def __init__(self, cfg: dict, run=None):
        self.owner = cfg["chat"]["github"]["owner"]
        self.repo = settings(cfg)["repo"]
        self.run = run or (lambda argv, input=None: proc.run(argv, input=input))

    def _gh(self, *args: str, input: str | None = None) -> str:
        try:
            return self.run(["gh", *args], input=input)
        except proc.ProcError as e:
            if "rate limit" in e.stderr.lower():
                raise ChatError("GitHub API rate limit reached; the next run tries again: " + redact(e.stderr)) from None
            raise ChatError(redact(str(e))) from None

    def _comments_path(self, channel: str) -> str:
        repo, n = channel.rsplit("#", 1)
        return f"repos/{repo}/issues/{n}/comments"

    def open_thread(self, ticket: Ticket) -> ThreadRef:
        n = ticket.key.split("-", 1)[1]
        channel = f"{self.repo}#{n}"
        ids = [int(i) for i in self._gh("api", "--paginate", self._comments_path(channel), "--jq", ".[].id").split()]
        return ThreadRef(channel, str(max(ids, default=0)))

    def post(self, thread: ThreadRef, text: str) -> str:
        out = self._gh("api", "-X", "POST", self._comments_path(thread.channel), "--input", "-", "--jq", ".id",
                       input=json.dumps({"body": text + "\n\n" + MARKER}))
        return out.strip()

    def ask(self, thread: ThreadRef, question: str, options: list[str], recommendation: int) -> str:
        lines = [question] + [f"{i}. {o}" for i, o in enumerate(options, 1)]
        lines.append(f"Recommended: {recommendation}. Reply with a number, or write your own answer.")
        return self.post(thread, "\n".join(lines))

    def poll(self, threads: dict[str, ThreadRef], since: dict[str, str]) -> list[Reply]:
        out: list[Reply] = []
        for key, th in threads.items():
            floor = int(since.get(key, th.ts))
            raw = self._gh("api", "--paginate", self._comments_path(th.channel), "--jq", ".[]")
            for c in map(json.loads, filter(None, raw.splitlines())):
                body = c.get("body") or ""
                if (c["id"] > floor and c["user"]["login"].lower() == self.owner.lower()
                        and MARKER not in body):
                    out.append(Reply(key, self.owner, body, str(c["id"])))
        return sorted(out, key=lambda r: int(r.ts))

    def auth_test(self) -> str:
        return self._gh("api", "user", "--jq", ".login").strip()

    def channel_check(self) -> str | None:
        if not json.loads(self._gh("api", f"repos/{self.repo}")).get("has_issues"):
            return f"issues are turned off in {self.repo}"
        return None
