from __future__ import annotations

from .base import GitFlow, GitFlowError
from .merge_to_test_branch import MergeToTestBranch
from .pr_only import PrOnly
from .squash_on_green import SquashOnGreen

FLOWS = {"pr-only": PrOnly, "squash-on-green": SquashOnGreen, "merge-to-test-branch": MergeToTestBranch}


def make_gitflow(cfg: dict) -> GitFlow:
    t = cfg["gitFlow"]["type"]
    if t not in FLOWS:
        raise GitFlowError(f"unknown gitFlow type {t!r}; valid: {', '.join(FLOWS)}")
    return FLOWS[t](cfg)
