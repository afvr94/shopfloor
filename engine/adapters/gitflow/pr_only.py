from __future__ import annotations

from .base import GitFlow


class PrOnly(GitFlow):
    def can_merge(self, repo_name: str, pr: dict) -> tuple[bool, str]:
        return False, "merging is off (pr-only)"
