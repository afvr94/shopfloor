from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from engine.lib import paths, worktrees
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
    def test_prepare_creates_from_origin_base_and_is_idempotent(self):
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertEqual(d, self.wt())
        self.assertEqual(git(d, "rev-parse", "--abbrev-ref", "HEAD"), "shopfloor/abc-1-x")
        (d / "wip.txt").write_text("keep")
        self.assertEqual(worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x"), d)
        self.assertEqual((d / "wip.txt").read_text(), "keep")

    def test_prepare_attaches_existing_remote_branch(self):
        git(self.clone, "checkout", "-b", "shopfloor/abc-1-x")
        commit_file(self.clone, "f.txt", "remote\n")
        git(self.clone, "push", "origin", "shopfloor/abc-1-x")
        git(self.clone, "checkout", "main")
        git(self.clone, "branch", "-D", "shopfloor/abc-1-x")
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertEqual((d / "f.txt").read_text(), "remote\n")
        self.assertEqual(git(d, "rev-parse", "--abbrev-ref", "@{u}"), "origin/shopfloor/abc-1-x")

    def test_prepare_replaces_broken_worktree_saving_patch(self):
        d = self.wt()
        d.mkdir(parents=True)
        git(d, "init", "-b", "main")
        commit_file(d, "a.txt", "a\n")
        (d / "junk.txt").write_text("x")
        out = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertFalse((out / "junk.txt").exists())
        self.assertEqual(git(out, "rev-parse", "--abbrev-ref", "HEAD"), "shopfloor/abc-1-x")
        self.assertEqual(len(list(paths.ticket_dir("crew", "ABC-1").glob("salvage-app-*.patch"))), 1)

    def test_prepare_moves_aside_non_git_leftover(self):
        d = self.wt()
        d.mkdir(parents=True)
        (d / "junk.txt").write_text("x")
        out = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertFalse((out / "junk.txt").exists())
        (aside,) = paths.ticket_dir("crew", "ABC-1").glob("salvage-app-*")
        self.assertEqual((aside / "junk.txt").read_text(), "x")

    def test_prepare_keeps_unpushed_local_commits(self):
        git(self.clone, "checkout", "-b", "shopfloor/abc-1-x")
        git(self.clone, "push", "origin", "shopfloor/abc-1-x")
        commit_file(self.clone, "local.txt", "unpushed\n")
        git(self.clone, "checkout", "main")
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertEqual((d / "local.txt").read_text(), "unpushed\n")

    def test_prepare_replaces_worktree_on_wrong_branch_and_saves_changes(self):
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/old")
        (d / "new.txt").write_text("precious\n")
        out = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        self.assertEqual(git(out, "rev-parse", "--abbrev-ref", "HEAD"), "shopfloor/abc-1-x")
        (patch,) = paths.ticket_dir("crew", "ABC-1").glob("salvage-app-*.patch")
        self.assertIn("precious", patch.read_text())

    def test_remove(self):
        d = worktrees.prepare(self.cfg, "ABC-1", "app", "shopfloor/abc-1-x")
        worktrees.remove(self.cfg, "ABC-1")
        self.assertFalse(d.exists())
        self.assertNotIn(str(d), git(self.clone, "worktree", "list"))
        worktrees.remove(self.cfg, "ABC-1")  # none left: still fine


if __name__ == "__main__":
    unittest.main()
