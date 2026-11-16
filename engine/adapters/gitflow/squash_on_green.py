from __future__ import annotations

from . import base
from .base import GitFlow


class SquashOnGreen(GitFlow):
    def can_merge(self, repo_name: str, pr: dict) -> tuple[bool, str]:
        if pr.get("isDraft"):
            return False, "draft"
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            return False, "a reviewer asked for changes"
        if pr.get("mergeable") != "MERGEABLE":
            return False, f"not mergeable ({pr.get('mergeable')})"
        if not base.checks_green(pr.get("statusCheckRollup") or []):
            return False, "checks not green"
        hit = base.denied_path(pr, self.flow["mergeDenyPaths"])
        if hit:
            return False, f"touches {hit}"
        return True, "ok"

    def merge(self, key: str, repo_name: str, pr: dict) -> str:
        self._require_mergeable(repo_name, pr)
        out = base.GH(["pr", "merge", str(pr["number"]), "--squash", "--match-head-commit",
                       pr["headRefOid"], "--delete-branch", "-R", self.cfg["repos"][repo_name]["github"]])
        return self._log(key, out.strip() or f"squash-merged #{pr['number']}")
