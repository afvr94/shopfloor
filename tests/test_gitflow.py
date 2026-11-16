from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from engine.adapters import gitflow
from engine.adapters.gitflow import base
from engine.lib import paths, state, transport, worktrees
from engine.lib.proc import ProcError
from tests.gitrepo import IDENT, commit_file, git, make_repo, tmpdir


class FakeGH:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []
        self.inputs: list = []

    def __call__(self, args, input=None):
        self.calls.append(args)
        self.inputs.append(input)
        for prefix, ans in self.answers.items():
            if " ".join(args).startswith(prefix):
                if isinstance(ans, Exception):
                    raise ans
                return ans
        return ""


class GitFlowCase(unittest.TestCase):
    flow = "pr-only"
    test_branch = None
    deny: list = []

    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {**IDENT, "SHOPFLOOR_HOME": str(root / "home")})
        p.start()
        self.addCleanup(p.stop)
        self.origin, self.clone = make_repo(root, extra_branches=["staging"])
        self.cfg = {
            "id": "crew", "protectedBranches": ["main", "staging"],
            "repos": {"app": {"github": "o/app", "base": "main", "path": str(self.clone)}},
            "gitFlow": {"type": self.flow, "testBranch": self.test_branch, "mergeDenyPaths": self.deny},
        }
        self.gh = FakeGH()
        g = mock.patch.object(base, "GH", self.gh)
        g.start()
        self.addCleanup(g.stop)
        self.pf = gitflow.make_gitflow(self.cfg)

    def ticket_clone(self, key="ABC-1", branch="shopfloor/abc-1-x"):
        state.save_ticket("crew", key, branch=branch)
        return worktrees.prepare(self.cfg, key, "app", branch)

    def hand_over(self, d, key="ABC-1", *refs):
        """What the crew does in its sandbox before sf push: bundle its branch into the ticket folder."""
        git(d, "bundle", "create", str(paths.ticket_dir("crew", key) / "out-app.bundle"), *(refs or ["HEAD"]))

    def work(self, branch="shopfloor/abc-1-x", fname="f.txt", text="x\n"):
        d = self.ticket_clone(branch=branch)
        commit_file(d, fname, text)
        self.hand_over(d, "ABC-1", branch)
        self.pf.push("ABC-1", "app")
        return d


class TestChecks(unittest.TestCase):
    def test_checks_green(self):
        ok = [{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}, {"conclusion": "NEUTRAL"}]
        self.assertTrue(base.checks_green(ok))
        self.assertFalse(base.checks_green([]))
        self.assertFalse(base.checks_green(ok + [{"conclusion": "FAILURE"}]))
        self.assertFalse(base.checks_green([{"status": "IN_PROGRESS", "conclusion": ""}]))
        self.assertTrue(base.checks_green([{"state": "SUCCESS"}]))

    def test_pr_info(self):
        gh = FakeGH({"pr view 7": json.dumps({"number": 7})})
        with mock.patch.object(base, "GH", gh):
            self.assertEqual(base.pr_info("o/app", 7), {"number": 7})
        self.assertIn("-R", gh.calls[0])


class TestShip(GitFlowCase):
    def test_ship_pushes_and_opens_pr_once(self):
        d = self.ticket_clone()
        commit_file(d, "f.txt", "x\n")
        self.hand_over(d, "ABC-1", "shopfloor/abc-1-x")
        self.gh.answers = {"pr list": "[]", "pr create": "https://github.com/o/app/pull/5\n"}
        url = self.pf.ship("ABC-1", "app", "T", "body")
        self.assertEqual(url, "https://github.com/o/app/pull/5")
        self.assertEqual(git(self.origin, "rev-parse", "shopfloor/abc-1-x"), git(d, "rev-parse", "HEAD"))
        create = [c for c in self.gh.calls if c[:2] == ["pr", "create"]][0]
        self.assertEqual(create[create.index("--body-file") + 1], "-")
        self.assertNotIn("body", create)
        self.assertIn("body", self.gh.inputs)
        self.gh.answers = {"pr list": json.dumps([{"url": "https://github.com/o/app/pull/5"}])}
        self.gh.calls.clear()
        self.assertEqual(self.pf.ship("ABC-1", "app", "T", "body"), "https://github.com/o/app/pull/5")
        self.assertEqual([c for c in self.gh.calls if c[:2] == ["pr", "create"]], [])

    def test_ship_lookup_failure_does_not_create_duplicate(self):
        self.work()
        self.gh.answers = {"pr list": ProcError(["gh"], 1, "boom")}
        with self.assertRaises(base.GitFlowError):
            self.pf.ship("ABC-1", "app", "T", "b")
        self.assertEqual([c for c in self.gh.calls if c[:2] == ["pr", "create"]], [])

    def test_ship_empty_create_output_raises(self):
        self.work()
        self.gh.answers = {"pr list": "[]", "pr create": "\n"}
        with self.assertRaises(base.GitFlowError):
            self.pf.ship("ABC-1", "app", "T", "b")

    def test_ship_refuses_protected_base_and_non_ticket_branches(self):
        for b in ("staging", "main", "feature/x", "shopfloor", "shopfloor/", "shopfloor/a..b", "shopfloor/a b",
                  "xshopfloor/a", ""):
            state.save_ticket("crew", "ABC-2", branch=b)
            with self.subTest(b), mock.patch.object(transport, "push") as push, \
                    self.assertRaises(base.GitFlowError):
                self.pf.ship("ABC-2", "app", "T", "b")
            push.assert_not_called()
        self.assertEqual(self.gh.calls, [])


class TestBundlePush(GitFlowCase):
    def test_only_the_recorded_branch_is_pushed_from_a_bundle_carrying_more(self):
        d = self.ticket_clone()
        commit_file(d, "f.txt", "x\n")
        git(d, "branch", "main", "-f", "HEAD")
        git(d, "branch", "shopfloor/other")
        self.hand_over(d, "ABC-1", "shopfloor/abc-1-x", "main", "shopfloor/other")
        before = git(self.origin, "rev-parse", "main")
        self.assertEqual(self.pf.push("ABC-1", "app"), "shopfloor/abc-1-x")
        self.assertEqual(git(self.origin, "rev-parse", "shopfloor/abc-1-x"), git(d, "rev-parse", "HEAD"))
        self.assertEqual(git(self.origin, "rev-parse", "main"), before)
        self.assertNotIn("shopfloor/other", git(self.origin, "branch", "--list"))

    def test_a_bundle_naming_another_branch_is_refused(self):
        d = self.ticket_clone()
        git(d, "checkout", "-q", "-b", "shopfloor/abc-1-y")
        commit_file(d, "f.txt", "x\n")
        self.hand_over(d, "ABC-1", "shopfloor/abc-1-y")
        with self.assertRaises(base.GitFlowError) as ctx:
            self.pf.push("ABC-1", "app")
        self.assertIn("does not carry shopfloor/abc-1-x", str(ctx.exception))
        self.assertEqual(git(self.origin, "branch", "--list", "shopfloor/*"), "")

    def test_a_symlinked_or_missing_bundle_is_refused(self):
        self.ticket_clone()
        out = paths.ticket_dir("crew", "ABC-1") / "out-app.bundle"
        with self.assertRaises(base.GitFlowError):
            self.pf.push("ABC-1", "app")
        out.symlink_to(paths.crew_config_path("crew"))
        with self.assertRaises(base.GitFlowError):
            self.pf.push("ABC-1", "app")

    def test_lease_is_what_transport_last_saw_on_origin(self):
        d = self.work()
        other = Path(self.tmp.name) / "other"
        git(self.tmp.name, "clone", "-q", "-b", "shopfloor/abc-1-x", str(self.origin), str(other))
        commit_file(other, "theirs.txt", "t\n")
        git(other, "push", "-q", "origin", "shopfloor/abc-1-x")
        theirs = git(self.origin, "rev-parse", "shopfloor/abc-1-x")
        commit_file(d, "mine.txt", "m\n")
        self.hand_over(d, "ABC-1", "shopfloor/abc-1-x")
        with mock.patch.object(transport.gitsafe, "run", wraps=transport.gitsafe.run) as run, \
                self.assertRaises(base.GitFlowError):
            self.pf.push("ABC-1", "app")
        seen = transport.sha(self.cfg, "app", "refs/heads/shopfloor/abc-1-x")
        (push,) = [c.args[0] for c in run.call_args_list if "push" in c.args[0]]
        self.assertIn(f"--force-with-lease=refs/heads/shopfloor/abc-1-x:{seen}", push)
        self.assertNotEqual(seen, theirs)
        self.assertEqual(git(self.origin, "rev-parse", "shopfloor/abc-1-x"), theirs)


class TestPrOnly(GitFlowCase):
    def test_pr_only_never_merges(self):
        self.assertEqual(self.pf.can_merge("app", {}), (False, "merging is off (pr-only)"))


PR = {"number": 9, "headRefName": "shopfloor/abc-1-x", "headRefOid": "deadbeef", "isDraft": False,
      "mergeable": "MERGEABLE", "statusCheckRollup": [{"conclusion": "SUCCESS"}], "files": [{"path": "src/a.py"}]}


class TestSquash(GitFlowCase):
    flow = "squash-on-green"
    deny = ["infra/*"]

    def test_squash_requires_green_mergeable_not_draft_no_denied_paths(self):
        self.assertEqual(self.pf.can_merge("app", PR), (True, "ok"))
        self.assertFalse(self.pf.can_merge("app", {**PR, "isDraft": True})[0])
        self.assertFalse(self.pf.can_merge("app", {**PR, "mergeable": "CONFLICTING"})[0])
        self.assertFalse(self.pf.can_merge("app", {**PR, "statusCheckRollup": []})[0])
        self.assertEqual(self.pf.can_merge("app", {**PR, "reviewDecision": "CHANGES_REQUESTED"}),
                         (False, "a reviewer asked for changes"))
        self.assertEqual(self.pf.can_merge("app", {**PR, "reviewDecision": "APPROVED"}), (True, "ok"))
        self.assertEqual(self.pf.can_merge("app", {**PR, "files": [{"path": "infra/deploy.yml"}]}),
                         (False, "touches infra/deploy.yml"))
        self.assertEqual(self.pf.can_merge("app", {**PR, "files": ["infra/deploy.yml"]})[1], "touches infra/deploy.yml")

    def test_squash_merge_refuses_when_not_mergeable(self):
        with self.assertRaises(base.GitFlowError):
            self.pf.merge("ABC-1", "app", {**PR, "isDraft": True})
        self.assertEqual(self.gh.calls, [])

    def test_unknown_type(self):
        with self.assertRaises(base.GitFlowError) as ctx:
            gitflow.make_gitflow({**self.cfg, "gitFlow": {"type": "yolo"}})
        self.assertIn("pr-only", str(ctx.exception))

    def test_squash_pins_head_sha(self):
        self.gh.answers = {"pr merge": "Merged\n"}
        out = self.pf.merge("ABC-1", "app", PR)
        self.assertEqual(out, "Merged")
        call = self.gh.calls[0]
        self.assertEqual(call[:7], ["pr", "merge", "9", "--squash", "--match-head-commit", "deadbeef", "--delete-branch"])
        self.assertIn("o/app", call)


class TestMergeToTest(GitFlowCase):
    flow = "merge-to-test-branch"
    test_branch = "staging"
    deny = ["infra/*"]

    def pr(self, **kw):
        oid = git(self.origin, "rev-parse", "shopfloor/abc-1-x")
        return {**PR, "headRefOid": oid, **kw}

    def test_can_merge_table(self):
        flow = self.pf
        self.assertEqual(flow.can_merge("app", PR), (True, "ok"))
        for over, why in [
            ({"isDraft": True}, "draft"),
            ({"reviewDecision": "CHANGES_REQUESTED"}, "a reviewer asked for changes"),
            ({"files": [{"path": "infra/a.yml"}]}, "touches infra/a.yml"),
            ({"statusCheckRollup": [{"conclusion": "FAILURE"}]}, "checks not green"),
            ({"statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": ""}]}, "checks not green"),
            ({"statusCheckRollup": []}, "checks not green"),
        ]:
            self.assertEqual(flow.can_merge("app", {**PR, **over}), (False, why))
        self.cfg["gitFlow"]["testBranch"] = None
        self.assertEqual(flow.can_merge("app", PR), (False, "no testBranch configured"))

    def test_merge_refuses_when_not_mergeable(self):
        self.work()
        with self.assertRaises(base.GitFlowError):
            self.pf.merge("ABC-1", "app", self.pr(isDraft=True))
        self.assertEqual(git(self.origin, "rev-list", "--count", "staging"), "1")

    def test_merge_to_test_branch_no_ff_in_own_worktree(self):
        self.work()
        out = self.pf.merge("ABC-1", "app", self.pr())
        self.assertRegex(out, r"^merged shopfloor/abc-1-x into staging at [0-9a-f]{7}$")
        self.assertEqual(len(git(self.origin, "rev-list", "--parents", "-n1", "staging").split()), 3)
        self.assertTrue((paths.crew_state_dir("crew") / "merge" / "app" / ".git").is_dir())
        self.assertFalse((paths.worktrees_dir("crew") / "_test").exists())
        self.assertEqual(self.gh.calls, [])

    def test_merge_to_test_branch_conflict_aborts(self):
        self.work(fname="README", text="mine\n")
        other = Path(self.tmp.name) / "other"
        git(self.tmp.name, "clone", str(self.origin), str(other))
        git(other, "checkout", "staging")
        commit_file(other, "README", "theirs\n")
        git(other, "push", "origin", "staging")
        before = git(self.origin, "rev-parse", "staging")
        with self.assertRaises(base.GitFlowError) as ctx:
            self.pf.merge("ABC-1", "app", self.pr())
        self.assertIn("README", str(ctx.exception))
        self.assertEqual(git(self.origin, "rev-parse", "staging"), before)
        self.assertEqual(git(paths.crew_state_dir("crew") / "merge" / "app", "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
