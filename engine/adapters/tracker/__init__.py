from __future__ import annotations

from engine.adapters.tracker.linear import LinearTracker


def make_tracker(cfg: dict, post=None) -> LinearTracker:
    return LinearTracker(cfg, post=post)
