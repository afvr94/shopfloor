from __future__ import annotations

from engine.lib import paths
from engine.lib.proc import ProcError, run

from . import base
from .base import GitFlow, GitFlowError


class MergeToTestBranch(GitFlow):
    def can_merge(self, repo_name: str, pr: dict) -> tuple[bool, str]:
        if not self.flow.get("testBranch"):
            return False, "no testBranch configured"
        if pr.get("isDraft"):
            return False, "draft"
        hit = base.denied_path(pr, self.flow["mergeDenyPaths"])
        return (False, f"touches {hit}") if hit else (True, "ok")

    def merge(self, key: str, repo_name: str, pr: dict) -> str:
        test, head = self.flow["testBranch"], pr["headRefName"]
        main = self.cfg["repos"][repo_name]["path"]
        wt = paths.worktrees_dir(self.crew) / "_test" / repo_name

        def g(*a: str) -> str:
            return run(["git", "-C", str(wt), *a]).strip()

        try:
            run(["git", "-C", main, "fetch", "origin"])
            if not wt.exists():
                run(["git", "-C", main, "worktree", "prune"])
                wt.parent.mkdir(parents=True, exist_ok=True)
                run(["git", "-C", main, "worktree", "add", "--detach", str(wt), f"origin/{test}"])
            g("checkout", "-f", "-B", test, f"origin/{test}")
            try:
                g("merge", "--no-ff", f"origin/{head}", "-m", f"Merge {head} into {test} ({key})")
            except ProcError as e:
                run(["git", "-C", str(wt), "merge", "--abort"], check=False)
                raise GitFlowError(f"merge of {head} into {test} failed: {e.stderr.strip()[:200]}") from e
            g("push", "origin", test)
            sha = g("rev-parse", "--short=7", "HEAD")
        except ProcError as e:
            raise GitFlowError(str(e)) from e
        return self._log(key, f"merged {head} into {test} at {sha}")
