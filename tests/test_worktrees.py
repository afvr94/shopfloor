from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from engine.lib import gitsafe, paths, state, transport, worktrees
from tests.gitrepo import commit_file, git, make_repo, tmpdir, IDENT


class WorktreeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {**IDENT, "SHOPFLOOR_HOME": str(root / "home")})
        p.start()
        self.addCleanup(p.stop)
        self.origin, self.clone = make_repo(root)
        self.cfg = {"id": "crew", "repos": {"app": {"github": "o/app", "base": "main", "path": str(self.clone)}}}

    def wt(self):
        return paths.worktrees_dir("crew") / "ABC-1" / "app"


class TestBranchName(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(worktrees.branch_name("ABC-1", "Fix the Login!! bug"), "shopfloor/abc-1-fix-the-login-bug")
        self.assertEqual(worktrees.branch_name("ABC-1", "***"), "shopfloor/abc-1")
        b = worktrees.branch_name("ABC-1", "word " * 30)
        self.assertLessEqual(len(b.split("abc-1-", 1)[1]), 40)
        self.assertFalse(b.endswith("-"))


class TestPrepare(WorktreeCase):
    def bundle(self):
        return paths.ticket_dir("crew", "ABC-1") / "in-app.bundle"

    def test_prepare_clones_from_a_bundle_onto_the_ticket_branch_with_no_reachable_origin(self):
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertEqual(d, self.wt())
        self.assertTrue((d / ".git").is_dir())
        self.assertEqual(git(d, "rev-parse", "--abbrev-ref", "HEAD"), "shopfloor/abc-1-x")
        self.assertEqual(git(d, "rev-parse", "HEAD"), git(self.origin, "rev-parse", "main"))
        self.assertEqual(git(d, "remote", "get-url", "origin"), transport.NO_REMOTE)
        self.assertNotIn(str(d), git(self.clone, "worktree", "list"))
        self.assertIn("refs/heads/main", git(d, "bundle", "list-heads", str(self.bundle())))

    def test_prepare_leaves_an_existing_clone_alone_and_refreshes_the_bundle(self):
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        (d / "wip.txt").write_text("keep")
        git(d, "config", "core.editor", "vi")
        commit_file(self.clone, "new.txt", "n\n")
        git(self.clone, "push", "-q", "origin", "main")
        with mock.patch.object(gitsafe, "run", wraps=gitsafe.run) as run:
            self.assertEqual(worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x"), d)
        self.assertNotIn(str(d), " ".join(" ".join(map(str, c.args[0])) + str(c.kwargs.get("cwd")) for c in run.call_args_list))
        self.assertEqual((d / "wip.txt").read_text(), "keep")
        git(d, "fetch", str(self.bundle()), "+refs/heads/*:refs/remotes/origin/*")
        self.assertEqual(git(d, "rev-parse", "origin/main"), git(self.origin, "rev-parse", "main"))

    def test_prepare_attaches_existing_remote_branch(self):
        git(self.clone, "checkout", "-b", "shopfloor/abc-1-x")
        commit_file(self.clone, "f.txt", "remote\n")
        git(self.clone, "push", "origin", "shopfloor/abc-1-x")
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertEqual((d / "f.txt").read_text(), "remote\n")
        self.assertEqual(git(d, "rev-parse", "--abbrev-ref", "@{u}"), "origin/shopfloor/abc-1-x")
        self.assertIn("refs/heads/shopfloor/abc-1-x", git(d, "bundle", "list-heads", str(self.bundle())))

    def test_prepare_never_writes_through_a_planted_symlink(self):
        target = Path(self.tmp.name) / "victim"
        target.write_text("keep")
        tdir = paths.ticket_dir("crew", "ABC-1")
        state.make_dir(tdir)
        self.bundle().symlink_to(target)
        worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertEqual(target.read_text(), "keep")
        self.assertFalse(self.bundle().is_symlink())

    def test_remove(self):
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        worktrees.remove(self.cfg, "ABC-1")
        self.assertFalse(d.exists())
        worktrees.remove(self.cfg, "ABC-1")  # none left: still fine


if __name__ == "__main__":
    unittest.main()
