from __future__ import annotations

import fnmatch
import json
import os
import tempfile
from pathlib import Path

from engine.lib import state
from engine.lib.proc import ProcError, run

GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class GitFlowError(Exception):
    pass


def _gh(args: list[str]) -> str:
    return run(["gh", *args])


GH = _gh  # tests replace this seam


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

    def ship(self, key: str, repo_name: str, worktree: Path, title: str, body: str) -> str:
        repo = self.cfg["repos"][repo_name]
        branch = run(["git", "-C", str(worktree), "symbolic-ref", "--short", "HEAD"]).strip()
        if branch == repo["base"] or branch in self.cfg.get("protectedBranches", []):
            raise GitFlowError(f"refusing to push protected branch {branch}")
        run(["git", "-C", str(worktree), "push", "-u", "origin", branch])
        try:
            return json.loads(GH(["pr", "view", branch, "-R", repo["github"], "--json", "url"]))["url"]
        except ProcError:
            pass
        fd, name = tempfile.mkstemp(suffix=".md")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(body)
            out = GH(["pr", "create", "-R", repo["github"], "--base", repo["base"], "--head", branch,
                      "--title", title, "--body-file", name])
        finally:
            os.unlink(name)
        return out.strip().splitlines()[-1]

    def can_merge(self, repo_name: str, pr: dict) -> tuple[bool, str]:
        return False, "merging is off"

    def merge(self, key: str, repo_name: str, pr: dict) -> str:
        raise GitFlowError("merging is off")

    def _log(self, key: str, note: str) -> str:
        state.log_run(self.crew, key, "merged", note)
        return note
