from __future__ import annotations

import json
import unittest

from engine.adapters.tracker import make_tracker
from engine.adapters.tracker.base import TrackerError
from engine.adapters.tracker.github import MARKER, GitHubTracker
from engine.lib.proc import ProcError
from tests.fakes import FakeGh

CFG = {
    "tracker": {
        "type": "github",
        "github": {
            "repo": "acme/app",
            "label": "shopfloor",
            "assignee": None,
            "holdLabels": ["hold"],
            "states": {"inProgress": "shopfloor:in-progress", "review": "shopfloor:review"},
        },
        "mayMove": ["todo->inProgress", "inProgress->review"],
    }
}


def cfg_with(**over):
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["tracker"]["github"].update(over)
    return cfg


def issue(n, labels=(), state="open", pr=False, body="b", assignees=(), created="2026-01-01T00:00:00Z"):
    d = {
        "number": n, "node_id": f"node{n}", "title": f"t{n}", "body": body, "state": state,
        "html_url": f"https://github.com/acme/app/issues/{n}", "created_at": created,
        "labels": [{"name": x} for x in labels], "assignees": [{"login": a} for a in assignees],
    }
    if pr:
        d["pull_request"] = {}
    return d


def lines(*issues):
    return "\n".join(json.dumps(i) for i in issues) + "\n"


def tracker(answers, cfg=CFG):
    fake = FakeGh(answers)
    return make_tracker(cfg, run=fake), fake


def writes(fake):
    return [a for a, _ in fake.calls if "-X" in a]


class GitHubTrackerTests(unittest.TestCase):
    def test_make_tracker_picks_github(self):
        self.assertIsInstance(make_tracker(CFG, run=FakeGh([])), GitHubTracker)

    def test_candidates_query(self):
        t, fake = tracker([""])
        t.list_candidates()
        self.assertEqual(fake.calls[0][0], [
            "gh", "api", "--paginate",
            "repos/acme/app/issues?state=open&labels=shopfloor&per_page=100&sort=created&direction=asc",
            "--jq", ".[]"])

    def test_candidates_filter(self):
        t, _ = tracker([lines(issue(1), issue(2, pr=True), issue(3, ["shopfloor:in-progress"]),
                              issue(4, ["HOLD"]), issue(5, ["Shopfloor:Review"]), issue(6, ["Shopfloor"]))])
        self.assertEqual([x.key for x in t.list_candidates()], ["GH-1", "GH-6"])

    def test_candidates_oldest_first(self):
        t, _ = tracker([lines(issue(1, created="2026-02-01T00:00:00Z"), issue(2, created="2026-01-01T00:00:00Z"))])
        self.assertEqual([x.key for x in t.list_candidates()], ["GH-2", "GH-1"])

    def test_candidates_assignee_me(self):
        t, _ = tracker([lines(issue(1, assignees=["bob"]), issue(2, assignees=["ana"])), "ana\n"],
                       cfg_with(assignee="me"))
        self.assertEqual([x.key for x in t.list_candidates()], ["GH-2"])

    def test_candidates_assignee_login(self):
        t, _ = tracker([lines(issue(1, assignees=["bob"]), issue(2, assignees=["ana"]))], cfg_with(assignee="bob"))
        self.assertEqual([x.key for x in t.list_candidates()], ["GH-1"])

    def test_candidates_assignee_ignores_case(self):
        t, _ = tracker([lines(issue(1, assignees=["bob"]), issue(2, assignees=["ana"]))], cfg_with(assignee="Ana"))
        self.assertEqual([x.key for x in t.list_candidates()], ["GH-2"])
        t, _ = tracker([lines(issue(1, assignees=["bob"]), issue(2, assignees=["Ana"])), "ana\n"],
                       cfg_with(assignee="me"))
        self.assertEqual([x.key for x in t.list_candidates()], ["GH-2"])

    def test_closed_flag(self):
        t, _ = tracker([json.dumps(issue(1, state="closed")), json.dumps(issue(2))])
        self.assertEqual((t.get("GH-1").closed, t.get("GH-2").closed), (True, False))

    def test_get_maps_ticket(self):
        t, fake = tracker([json.dumps(issue(42, ["shopfloor", "x"], assignees=["ana", "bob"]))])
        tk = t.get("gh-42")
        self.assertEqual(fake.calls[0][0], ["gh", "api", "repos/acme/app/issues/42"])
        self.assertEqual((tk.key, tk.id, tk.title, tk.body, tk.labels, tk.state, tk.url, tk.assignee,
                          tk.relations, tk.created_at),
                         ("GH-42", "node42", "t42", "b", ["shopfloor", "x"], "todo",
                          "https://github.com/acme/app/issues/42", "ana", [], "2026-01-01T00:00:00Z"))

    def test_get_empty_body_and_assignee(self):
        t, _ = tracker([json.dumps(issue(1, body=None))])
        tk = t.get("GH-1")
        self.assertEqual((tk.body, tk.assignee), ("", None))

    def test_get_bad_key(self):
        t, fake = tracker([])
        with self.assertRaisesRegex(TrackerError, "GH-42"):
            t.get("ENG-1")
        self.assertEqual(fake.calls, [])

    def test_stage_prefers_review(self):
        t, _ = tracker([json.dumps(issue(1, ["shopfloor:in-progress", "Shopfloor:Review"]))])
        self.assertEqual(t.get("GH-1").state, "review")

    def test_transition_swaps_labels(self):
        t, fake = tracker([json.dumps(issue(7, ["shopfloor"])), ""])
        t.transition("GH-7", "inProgress")
        self.assertEqual(fake.calls[1], (["gh", "api", "-X", "POST", "repos/acme/app/issues/7/labels", "--input", "-"],
                                         json.dumps({"labels": ["shopfloor:in-progress"]})))
        t, fake = tracker([json.dumps(issue(7, ["shopfloor", "shopfloor:in-progress"])), "", ""])
        t.transition("GH-7", "review")
        self.assertEqual(fake.calls[1][1], json.dumps({"labels": ["shopfloor:review"]}))
        self.assertEqual(fake.calls[2], (["gh", "api", "-X", "DELETE",
                                          "repos/acme/app/issues/7/labels/shopfloor%3Ain-progress"], None))

    def test_transition_remove_404_ok(self):
        t, _ = tracker([json.dumps(issue(7, ["shopfloor", "shopfloor:in-progress"])), "",
                        ProcError(["gh"], 1, "HTTP 404: Not Found")])
        t.transition("GH-7", "review")

    def test_transition_remove_other_error_raises(self):
        t, _ = tracker([json.dumps(issue(7, ["shopfloor", "shopfloor:in-progress"])), "",
                        ProcError(["gh"], 1, "HTTP 500")])
        with self.assertRaises(TrackerError):
            t.transition("GH-7", "review")

    def test_transition_same_stage_no_write(self):
        t, fake = tracker([json.dumps(issue(7, ["shopfloor", "shopfloor:in-progress"]))])
        t.transition("GH-7", "inProgress")
        self.assertEqual(len(fake.calls), 1)

    def test_transition_checks(self):
        cases = [
            (issue(7, []), "inProgress"),
            (issue(7, ["shopfloor", "hold"]), "inProgress"),
            (issue(7, ["shopfloor"], state="closed"), "inProgress"),
            (issue(7, ["shopfloor", "shopfloor:review"]), "todo"),
            (issue(7, ["shopfloor"]), "bogus"),
        ]
        for it, to in cases:
            with self.subTest(to=to, labels=it["labels"]):
                t, fake = tracker([json.dumps(it)])
                with self.assertRaises(TrackerError):
                    t.transition("GH-7", to)
                self.assertEqual(writes(fake), [])

    def test_bodies_on_stdin(self):
        t, fake = tracker(["991\n"])
        self.assertEqual(t.comment("GH-7", "body kept off argv"), "991")
        argv, stdin = fake.calls[0]
        self.assertEqual(argv, ["gh", "api", "-X", "POST", "repos/acme/app/issues/7/comments",
                                "--input", "-", "--jq", ".id"])
        self.assertEqual(stdin, json.dumps({"body": "body kept off argv\n\n" + MARKER}))
        self.assertFalse(any("kept off argv" in a for a in argv))

    def test_update_comment(self):
        t, fake = tracker([json.dumps({"issue_url": "https://api.github.com/repos/acme/app/issues/7",
                                       "body": "old\n\n" + MARKER + "\n"}), ""])
        t.update_comment("GH-7", "55", "x")
        self.assertEqual(fake.calls, [
            (["gh", "api", "repos/acme/app/issues/comments/55"], None),
            (["gh", "api", "-X", "PATCH", "repos/acme/app/issues/comments/55", "--input", "-"],
             json.dumps({"body": "x\n\n" + MARKER}))])

    def test_comment_and_update_carry_marker(self):
        t, fake = tracker(["1\n", json.dumps({"issue_url": "https://api.github.com/repos/acme/app/issues/7",
                                              "body": "old\n\n" + MARKER}), ""])
        t.comment("GH-7", "a")
        t.update_comment("GH-7", "55", "b")
        self.assertTrue(fake.calls[0][1].endswith('\\n\\n' + MARKER + '"}'))
        self.assertTrue(fake.calls[2][1].endswith('\\n\\n' + MARKER + '"}'))

    def test_update_comment_rejects_non_numeric_ids(self):
        for bad in ("1/../../repos/other/repo/issues/5", "12?x", "12#x", "", " 12", "١٢"):
            with self.subTest(bad=bad):
                t, fake = tracker([])
                with self.assertRaisesRegex(TrackerError, "is not a GitHub comment id"):
                    t.update_comment("GH-7", bad, "x")
                self.assertEqual(fake.calls, [])

    def test_update_comment_refuses_another_issues_comment(self):
        for url in ("https://api.github.com/repos/acme/app/issues/70", "https://api.github.com/repos/acme/app/issues/8"):
            with self.subTest(url=url):
                t, fake = tracker([json.dumps({"issue_url": url})])
                with self.assertRaisesRegex(TrackerError, "GH-7"):
                    t.update_comment("GH-7", "55", "x")
                self.assertEqual(writes(fake), [])

    def test_update_comment_refuses_the_owners_own_comment(self):
        url = "https://api.github.com/repos/acme/app/issues/7"
        for body in ("my words", "quoting " + MARKER + " mid-text", None):
            with self.subTest(body=body):
                t, fake = tracker([json.dumps({"issue_url": url, "body": body})])
                with self.assertRaisesRegex(TrackerError, "not one of Shopfloor's"):
                    t.update_comment("GH-7", "55", "x")
                self.assertEqual(writes(fake), [])

    def test_link_pr_idempotent(self):
        url = "https://github.com/acme/app/pull/9"
        t, fake = tracker(["hello\nsee " + url + "\n"])
        t.link_pr("GH-7", url)
        self.assertEqual(fake.calls[0][0], ["gh", "api", "--paginate", "repos/acme/app/issues/7/comments",
                                            "--jq", ".[].body"])
        self.assertEqual(writes(fake), [])
        t, fake = tracker(["hello\n", "1\n"])
        t.link_pr("GH-7", url)
        self.assertEqual(fake.calls[1][1], json.dumps({"body": "Pull request: " + url + "\n\n" + MARKER}))

    def test_writes_not_retried(self):
        t, fake = tracker([ProcError(["gh"], 1, "HTTP 502")])
        with self.assertRaises(TrackerError):
            t.comment("GH-7", "x")
        self.assertEqual(len(fake.calls), 1)

    def test_rate_limit_message(self):
        t, _ = tracker([ProcError(["gh"], 1, "API rate limit exceeded for user")])
        with self.assertRaisesRegex(TrackerError, "^GitHub API rate limit reached"):
            t.viewer()

    def test_team_check(self):
        t, fake = tracker([json.dumps({"has_issues": False}), "shopfloor\n"])
        self.assertEqual(t.team_check(), [
            "issues are turned off in acme/app",
            "label 'shopfloor:in-progress' not found in acme/app",
            "label 'shopfloor:review' not found in acme/app"])
        self.assertEqual(fake.calls[0][0], ["gh", "api", "repos/acme/app"])
        self.assertEqual(fake.calls[1][0], ["gh", "api", "--paginate", "repos/acme/app/labels",
                                            "--jq", ".[].name"])
        t, _ = tracker([json.dumps({"has_issues": True}), "Shopfloor\nSHOPFLOOR:in-progress\nshopfloor:Review\n"])
        self.assertEqual(t.team_check(), [])

    def test_viewer(self):
        t, fake = tracker(["ana\n"])
        self.assertEqual(t.viewer(), "ana")
        self.assertEqual(fake.calls[0][0], ["gh", "api", "user", "--jq", ".login"])


if __name__ == "__main__":
    unittest.main()
