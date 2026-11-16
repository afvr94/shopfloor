from __future__ import annotations

import shutil

from engine.lib import gitsafe, paths, state, transport
from engine.lib.proc import ProcError

from . import base
from .base import GitFlow, GitFlowError


class MergeToTestBranch(GitFlow):
    def can_merge(self, repo_name: str, pr: dict) -> tuple[bool, str]:
        if not self.flow.get("testBranch"):
            return False, "no testBranch configured"
        if pr.get("isDraft"):
            return False, "draft"
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            return False, "a reviewer asked for changes"
        if not base.checks_green(pr.get("statusCheckRollup") or []):
            return False, "checks not green"
        hit = base.denied_path(pr, self.flow["mergeDenyPaths"])
        return (False, f"touches {hit}") if hit else (True, "ok")

    def merge(self, key: str, repo_name: str, pr: dict) -> str:
        self._require_mergeable(repo_name, pr)
        test, head, oid = self.flow["testBranch"], pr["headRefName"], pr["headRefOid"]
        # sf's own working copy, cloned from the transport repo; no crew can write here
        wt = paths.crew_state_dir(self.crew) / "merge" / repo_name

        def g(*a: str, check: bool = True) -> str:
            return gitsafe.git(wt, *a, check=check)

        try:
            transport.update(self.cfg, repo_name)
            if not (wt / ".git").is_dir():
                shutil.rmtree(wt, ignore_errors=True)
                state.make_dir(wt.parent)
                gitsafe.git(wt.parent, "clone", "--quiet", "--no-checkout",
                            str(transport.gitdir(self.crew, repo_name)), str(wt), local=True)
            g("fetch", "--quiet", "--prune", "origin")
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
            g("push", "--quiet", "origin", f"+HEAD:refs/crew/{test}")
            transport.push(self.cfg, repo_name, test)
            sha = g("rev-parse", "--short=7", "HEAD")
        except (ProcError, transport.TransportError) as e:
            raise GitFlowError(str(e)) from e
        return self._log(key, f"merged {head} into {test} at {sha}")
