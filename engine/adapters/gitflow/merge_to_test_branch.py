from __future__ import annotations

from engine.lib import gitsafe, paths, worktrees
from engine.lib.proc import ProcError

from . import base
from .base import GitFlow, GitFlowError


class MergeToTestBranch(GitFlow):
    def can_merge(self, repo_name: str, pr: dict) -> tuple[bool, str]:
        if not self.flow.get("testBranch"):
            return False, "no testBranch configured"
        if pr.get("isDraft"):
            return False, "draft"
        if not base.checks_green(pr.get("statusCheckRollup") or []):
            return False, "checks not green"
        hit = base.denied_path(pr, self.flow["mergeDenyPaths"])
        return (False, f"touches {hit}") if hit else (True, "ok")

    def merge(self, key: str, repo_name: str, pr: dict) -> str:
        self._require_mergeable(repo_name, pr)
        test, head, oid = self.flow["testBranch"], pr["headRefName"], pr["headRefOid"]
        main = self.cfg["repos"][repo_name]["path"]
        wt = paths.worktrees_dir(self.crew) / "_test" / repo_name

        def g(*a: str, check: bool = True) -> str:
            return gitsafe.git(wt, *a, check=check)

        try:
            if not (gitsafe.pinned(wt) and worktrees.is_clone(wt, main)):
                worktrees.drop(wt, main)
                worktrees.clone(main, wt)
            g("fetch", "--prune", "origin")
            g("checkout", "-f", "-B", test, f"origin/{test}")
            try:
                g("merge", "--no-ff", oid, "-m", f"Merge {head} into {test} ({key})")
            except ProcError as e:
                conflicts = g("diff", "--name-only", "--diff-filter=U", check=False)
                g("merge", "--abort", check=False)
                raise GitFlowError(
                    f"merge of {head} into {test} failed: {e.stderr.strip()[:200]}"
                    f" conflicts: {conflicts.replace(chr(10), ', ') or 'none listed'}"
                ) from e
            gitsafe.push(wt, main, test)
            sha = g("rev-parse", "--short=7", "HEAD")
        except ProcError as e:
            raise GitFlowError(str(e)) from e
        return self._log(key, f"merged {head} into {test} at {sha}")
