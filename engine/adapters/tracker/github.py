from __future__ import annotations

import json
import re
from urllib.parse import quote

from engine.adapters.tracker.base import STAGES, Ticket, TrackerError, check_move, settings
from engine.lib import proc
from engine.lib.redact import redact


MARKER = "<!-- shopfloor -->"


def _names(issue: dict) -> list[str]:
    return [l["name"] for l in issue["labels"]]


class GitHubTracker:
    def __init__(self, cfg: dict, run=None):
        self.cfg = cfg
        self.gh = settings(cfg)
        self.repo = self.gh["repo"]
        self.run = run or (lambda argv, input=None: proc.run(argv, input=input))

    def _gh(self, *args: str, input: str | None = None) -> str:
        try:
            return self.run(["gh", *args], input=input)
        except proc.ProcError as e:
            if "rate limit" in e.stderr.lower():
                raise TrackerError("GitHub API rate limit reached; the next run tries again: " + redact(e.stderr)) from None
            raise TrackerError(redact(str(e))) from None

    def _number(self, key: str) -> int:
        m = re.fullmatch(r"GH-(\d+)", key, re.IGNORECASE)
        if not m:
            raise TrackerError(f"{key!r} is not a GitHub issue key like GH-42")
        return int(m.group(1))

    def _stage(self, issue: dict) -> str:
        have = {n.lower() for n in _names(issue)}
        states = self.gh["states"]
        for stage in ("review", "inProgress"):
            if states[stage].lower() in have:
                return stage
        return "todo"

    def _ticket(self, n: dict) -> Ticket:
        return Ticket(
            key=f"GH-{n['number']}", id=n["node_id"], title=n["title"], body=n.get("body") or "",
            labels=_names(n), state=self._stage(n), url=n["html_url"],
            assignee=next((a["login"] for a in n.get("assignees") or []), None),
            relations=[], created_at=n["created_at"], closed=n["state"] == "closed",
        )

    def _issue(self, key: str) -> dict:
        return json.loads(self._gh("api", f"repos/{self.repo}/issues/{self._number(key)}"))

    def list_candidates(self) -> list[Ticket]:
        out = self._gh("api", "--paginate",
                       f"repos/{self.repo}/issues?state=open&labels={quote(self.gh['label'], safe='')}"
                       "&per_page=100&sort=created&direction=asc", "--jq", ".[]")
        skip = {n.lower() for n in [*self.gh.get("holdLabels", []), *self.gh["states"].values()]}
        issues = [i for i in map(json.loads, filter(None, out.splitlines()))
                  if "pull_request" not in i and not skip & {n.lower() for n in _names(i)}]
        who = self.gh.get("assignee")
        if who:
            who = (self.viewer() if who == "me" else who).lower()
            issues = [i for i in issues if any(a["login"].lower() == who for a in i.get("assignees") or [])]
        return sorted(map(self._ticket, issues), key=lambda t: t.created_at)

    def get(self, key: str) -> Ticket:
        return self._ticket(self._issue(key))

    def transition(self, key: str, to_key: str) -> None:
        if to_key not in STAGES:
            raise TrackerError(f"{to_key!r} is not one of {', '.join(STAGES)}")
        issue = self._issue(key)
        labels = {n.lower() for n in _names(issue)}
        if self.gh["label"].lower() not in labels:
            raise TrackerError(f"{key} lacks the {self.gh['label']!r} label")
        if labels & {h.lower() for h in self.gh.get("holdLabels", [])}:
            raise TrackerError(f"{key} carries a hold label")
        if issue["state"] != "open":
            raise TrackerError(f"{key} is closed")
        frm = self._stage(issue)
        if frm == to_key:  # a rebuild re-ships a ticket that is already there
            return
        check_move(self.cfg, frm, to_key)
        number = self._number(key)
        states = self.gh["states"]
        if to_key != "todo":
            self._gh("api", "-X", "POST", f"repos/{self.repo}/issues/{number}/labels", "--input", "-",
                     input=json.dumps({"labels": [states[to_key]]}))
        for stage, name in states.items():
            if stage == to_key or name.lower() not in labels:
                continue
            try:
                self._gh("api", "-X", "DELETE", f"repos/{self.repo}/issues/{number}/labels/{quote(name, safe='')}")
            except TrackerError as e:
                if "404" not in str(e):  # already gone
                    raise

    def comment(self, key: str, body: str) -> str:
        out = self._gh("api", "-X", "POST", f"repos/{self.repo}/issues/{self._number(key)}/comments",
                       "--input", "-", "--jq", ".id", input=json.dumps({"body": body + "\n\n" + MARKER}))
        return str(out.strip())

    def update_comment(self, key: str, comment_id: str, body: str) -> None:
        if not re.fullmatch(r"[0-9]+", comment_id):
            raise TrackerError(f"{comment_id!r} is not a GitHub comment id")
        found = json.loads(self._gh("api", f"repos/{self.repo}/issues/comments/{comment_id}"))
        if not str(found.get("issue_url", "")).endswith(f"/issues/{self._number(key)}"):
            raise TrackerError(f"comment {comment_id} is not on {key}")
        self._gh("api", "-X", "PATCH", f"repos/{self.repo}/issues/comments/{comment_id}", "--input", "-",
                 input=json.dumps({"body": body + "\n\n" + MARKER}))

    def link_pr(self, key: str, url: str) -> None:
        bodies = self._gh("api", "--paginate", f"repos/{self.repo}/issues/{self._number(key)}/comments",
                          "--jq", ".[].body")
        if url not in bodies:
            self.comment(key, "Pull request: " + url)

    def viewer(self) -> str:
        return self._gh("api", "user", "--jq", ".login").strip()

    def team_check(self) -> list[str]:
        problems = []
        if not json.loads(self._gh("api", f"repos/{self.repo}")).get("has_issues"):
            problems.append(f"issues are turned off in {self.repo}")
        have = {n.lower() for n in self._gh("api", "--paginate", f"repos/{self.repo}/labels",
                                            "--jq", ".[].name").splitlines()}
        wanted = [self.gh["label"], *self.gh["states"].values()]
        return problems + [f"label {n!r} not found in {self.repo}" for n in wanted if n.lower() not in have]
