from __future__ import annotations

import fnmatch
import json
import os
import re
import tempfile

from engine.lib import state, transport
from engine.lib.proc import ProcError, run

SHIP_BRANCH = re.compile(r"shopfloor/[A-Za-z0-9._/-]+")
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class GitFlowError(Exception):
    pass


def _gh(args: list[str]) -> str:
    return run(["gh", *args])


GH = _gh  # tests replace this seam


def gh_with_body(args: list[str], body: str) -> str:
    """gh with --body-file, so the text never sits in argv."""
    fd, name = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        return GH([*args, "--body-file", name])
    finally:
        os.unlink(name)


def checks_green(rollup: list[dict]) -> bool:
    return bool(rollup) and all((c.get("conclusion") or c.get("state")) in GREEN for c in rollup)


def pr_info(repo_github: str, number) -> dict:
    fields = "number,url,headRefName,headRefOid,isDraft,mergeable,statusCheckRollup,files"
    return json.loads(GH(["pr", "view", str(number), "-R", repo_github, "--json", fields]))


def denied_path(pr: dict, patterns: list[str]) -> str | None:
    for f in pr.get("files") or []:
        p = f["path"] if isinstance(f, dict) else f
        if any(fnmatch.fnmatch(p, pat) for pat in patterns):
            return p
    return None


class GitFlow:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.crew = cfg["id"]
        self.flow = cfg["gitFlow"]

    def ticket_branch(self, key: str) -> str:
        """The branch sf recorded for the ticket, if it is one a crew may push: shopfloor/*, never a base,
        protected or test branch."""
        branch = state.load_ticket(self.crew, key)["branch"]
        if not branch:
            raise GitFlowError(f"no branch recorded for {key}; run sf worktree prepare")
        bases = {r["base"] for r in self.cfg["repos"].values()}
        if not SHIP_BRANCH.fullmatch(branch) or ".." in branch or branch in bases or \
                branch == self.flow.get("testBranch") or branch in self.cfg.get("protectedBranches", []):
            raise GitFlowError(f"refusing to push {branch}; only shopfloor/* ticket branches ship")
        return branch

    def push(self, key: str, repo_name: str) -> str:
        """Push the ticket branch from the crew's out-<repo>.bundle, through the transport repo."""
        branch = self.ticket_branch(key)
        try:
            transport.receive(self.cfg, key, repo_name, branch)
            transport.push(self.cfg, repo_name, branch, force=True)
        except (transport.TransportError, ProcError) as e:
            raise GitFlowError(str(e)) from None
        return branch

    def ship(self, key: str, repo_name: str, title: str, body: str) -> str:
        repo = self.cfg["repos"][repo_name]
        branch = self.push(key, repo_name)
        try:
            found = json.loads(GH(["pr", "list", "-R", repo["github"], "--head", branch,
                                   "--state", "open", "--json", "url"]))
        except (ProcError, ValueError) as e:
            raise GitFlowError(f"could not look up existing PR for {branch}: {e}") from e
        if found:
            return found[0]["url"]
        out = gh_with_body(["pr", "create", "-R", repo["github"], "--base", repo["base"], "--head", branch,
                            "--title", title], body)
        lines = out.strip().splitlines()
        if not lines:
            raise GitFlowError(f"gh pr create printed no URL for {branch}")
        return lines[-1]

    def can_merge(self, repo_name: str, pr: dict) -> tuple[bool, str]:
        return False, "merging is off"

    def merge(self, key: str, repo_name: str, pr: dict) -> str:
        raise GitFlowError("merging is off")

    def _require_mergeable(self, repo_name: str, pr: dict) -> None:
        ok, why = self.can_merge(repo_name, pr)
        if not ok:
            raise GitFlowError(f"not merging: {why}")

    def _log(self, key: str, note: str) -> str:
        state.log_run(self.crew, key, "merged", note)
        return note
