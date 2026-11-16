from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from engine.lib import gitsafe, paths, state, worktrees
from tests.gitrepo import IDENT, commit_file, git, make_repo, tmpdir


class GitSafeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {**IDENT, "SHOPFLOOR_HOME": str(self.root / "home")})
        p.start()
        self.addCleanup(p.stop)
        self.origin, self.main = make_repo(self.root)
        cfg = {"id": "crew", "repos": {"app": {"github": "o/app", "base": "main", "path": str(self.main)}}}
        self.wt = worktrees.prepare(cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        commit_file(self.wt, "f.txt", "x\n")
        self.marker = self.root / "ran"

    def script(self, name: str) -> Path:
        p = self.root / name
        p.write_text(f"#!/bin/sh\ntouch {self.marker}\n")
        p.chmod(0o755)
        return p

    def assert_refused(self):
        with self.assertRaises(gitsafe.ConfigChanged):
            gitsafe.git(self.wt, "status")
        self.assertFalse(self.marker.exists())
        self.assertEqual(state.recent_runs("crew")[-1]["note"], "clone config changed; refusing")

    def test_planted_pre_push_hook_does_not_run(self):
        hook = self.wt / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\ntouch {self.marker}\nexit 1\n")
        hook.chmod(0o755)
        gitsafe.push(self.wt, str(self.main), "shopfloor/abc-1-x", force=True)
        self.assertFalse(self.marker.exists())
        self.assertEqual(git(self.origin, "rev-parse", "shopfloor/abc-1-x"), git(self.wt, "rev-parse", "HEAD"))

    def test_planted_fsmonitor_is_refused_and_never_run(self):
        git(self.wt, "config", "core.fsmonitor", str(self.script("mon")))
        self.assert_refused()
        gitsafe.raw(self.wt, "status")  # the pinned flags hold even without the config check
        self.assertFalse(self.marker.exists())

    def test_include_path_is_refused_and_never_followed(self):
        inc = self.root / "inc.cfg"
        inc.write_text(f"[core]\n\tfsmonitor = {self.script('mon')}\n")
        git(self.wt, "config", "include.path", str(inc))
        self.assert_refused()

    def test_rewritten_origin_is_refused_and_push_uses_the_main_checkout_url(self):
        decoy = self.root / "decoy.git"
        git(self.root, "init", "--bare", "-b", "main", str(decoy))
        git(self.wt, "remote", "set-url", "--push", "origin", str(decoy))
        self.assert_refused()
        gitsafe.pin(self.wt)  # even a clone whose rewritten config was accepted pushes to the real origin
        gitsafe.push(self.wt, str(self.main), "shopfloor/abc-1-x", force=True)
        self.assertEqual(git(decoy, "branch", "--list"), "")
        self.assertIn("shopfloor/abc-1-x", git(self.origin, "branch", "--list"))

    def test_ordinary_branch_work_keeps_the_pin(self):
        git(self.wt, "checkout", "-b", "shopfloor/abc-1-y", "origin/main")
        git(self.wt, "branch", "--set-upstream-to", "origin/main")
        git(self.wt, "config", "user.name", "Crew")
        gitsafe.git(self.wt, "status")

    def test_pin_lives_where_crews_cannot_write(self):
        pin = paths.crew_state_dir("crew") / "clones" / "ABC-1" / "app.sha256"
        self.assertTrue(pin.is_file())
        self.assertFalse(str(pin).startswith(str(paths.worktrees_dir("crew"))))
        self.assertNotIn("tickets", pin.parts)

    def test_global_credential_helper_is_kept(self):
        g = self.root / "global.cfg"
        g.write_text("[credential]\n\thelper = osxkeychain\n")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(g)}):
            flags = gitsafe.flags()
        self.assertEqual([f for f in flags if f.startswith("credential.")],
                         ["credential.helper=", "credential.helper=osxkeychain"])
        self.assertIn("core.hooksPath=/dev/null", flags)


if __name__ == "__main__":
    unittest.main()
