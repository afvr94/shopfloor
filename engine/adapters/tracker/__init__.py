from __future__ import annotations

from engine.adapters.tracker.github import GitHubTracker
from engine.adapters.tracker.linear import LinearTracker


def make_tracker(cfg: dict, post=None, run=None) -> LinearTracker | GitHubTracker:
    if cfg["tracker"]["type"] == "github":
        return GitHubTracker(cfg, run=run)
    return LinearTracker(cfg, post=post)
