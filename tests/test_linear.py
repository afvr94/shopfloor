from __future__ import annotations

import unittest
from unittest import mock

from engine.adapters.tracker import base, linear, make_tracker
from tests.fakes import FakeLinear

CFG = {
    "tracker": {
        "type": "linear",
        "tokenFile": "/nonexistent/secret",
        "linear": {
            "team": "ENG",
            "label": "shopfloor",
            "assignee": None,
            "holdLabels": ["hold"],
            "states": {"todo": "Todo", "inProgress": "In Progress", "review": "In Review"},
        },
        "mayMove": ["todo->inProgress", "inProgress->review"],
    }
}


def issue(key, labels=(), state="Todo", created="2026-01-01T00:00:00Z"):
    return {
        "id": "uuid-" + key, "identifier": key, "title": "t " + key, "description": "b",
        "url": "https://linear.app/x/" + key, "createdAt": created,
        "state": {"name": state}, "assignee": None,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "relations": {"nodes": [{"relatedIssue": {"identifier": "ENG-9"}}]},
    }


def page(nodes, more=False):
    return {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": "c" if more else None}}}


def cfg_with(**linear_over):
    import copy
    c = copy.deepcopy(CFG)
    c["tracker"]["linear"].update(linear_over)
    return c


class LinearTests(unittest.TestCase):
    def test_candidates_filter_label_state_team_and_skip_hold(self):
        fake = FakeLinear([page([issue("ENG-1"), issue("ENG-2", ["Hold"]), issue("ENG-3", ["bug"])])])
        out = linear.LinearTracker(CFG, post=fake).list_candidates()
        self.assertEqual([t.key for t in out], ["ENG-1", "ENG-3"])
        self.assertEqual(out[0].relations, ["ENG-9"])
        f = fake.calls[0][1]["filter"]
        self.assertEqual(f["team"], {"key": {"eq": "ENG"}})
        self.assertEqual(f["labels"], {"name": {"eqIgnoreCase": "shopfloor"}})
        self.assertEqual(f["state"], {"name": {"eq": "Todo"}})
        self.assertNotIn("assignee", f)

    def test_candidates_paginate(self):
        fake = FakeLinear([page([issue("ENG-1")], True), page([issue("ENG-2")], True), page([issue("ENG-3")])])
        out = linear.LinearTracker(CFG, post=fake).list_candidates()
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(fake.calls[1][1]["after"], "c")
        self.assertEqual([t.key for t in out], ["ENG-1", "ENG-2", "ENG-3"])

    def test_candidates_assignee_me(self):
        fake = FakeLinear([page([])])
        linear.LinearTracker(cfg_with(assignee="me"), post=fake).list_candidates()
        self.assertEqual(fake.calls[0][1]["filter"]["assignee"], {"isMe": {"eq": True}})

    def test_candidates_assignee_other(self):
        fake = FakeLinear([page([])])
        linear.LinearTracker(cfg_with(assignee="a@b.c"), post=fake).list_candidates()
        self.assertEqual(fake.calls[0][1]["filter"]["assignee"], {"email": {"eq": "a@b.c"}})

    def test_transition_checks_maymove(self):
        fake = FakeLinear([{"issue": issue("ENG-1", ["shopfloor"], state="In Review")}])
        with self.assertRaisesRegex(base.TrackerError, "move review->inProgress is not in tracker.mayMove"):
            linear.LinearTracker(CFG, post=fake).transition("ENG-1", "inProgress")
        self.assertEqual(len(fake.calls), 1)

    def test_transition_unknown_current_state(self):
        fake = FakeLinear([{"issue": issue("ENG-1", ["shopfloor"], state="Weird")}])
        with self.assertRaisesRegex(base.TrackerError, "Weird"):
            linear.LinearTracker(CFG, post=fake).transition("ENG-1", "inProgress")

    def test_transition_resolves_state_id(self):
        fake = FakeLinear([
            {"issue": issue("ENG-1", ["shopfloor"], state="Todo")},
            {"workflowStates": {"nodes": [{"id": "S1"}]}},
            {"issueUpdate": {"success": True}},
        ])
        linear.LinearTracker(CFG, post=fake).transition("ENG-1", "inProgress")
        self.assertEqual(fake.calls[1][1], {"filter": {"team": {"key": {"eq": "ENG"}}, "name": {"eq": "In Progress"}}})
        self.assertIn("issueUpdate", fake.calls[2][0])
        self.assertEqual(fake.calls[2][1], {"id": "uuid-ENG-1", "stateId": "S1"})

    def test_transition_missing_state(self):
        fake = FakeLinear([{"issue": issue("ENG-1", ["shopfloor"])}, {"workflowStates": {"nodes": []}}])
        with self.assertRaisesRegex(base.TrackerError, "In Progress"):
            linear.LinearTracker(CFG, post=fake).transition("ENG-1", "inProgress")

    def test_candidates_sorted_oldest_first_across_pages(self):
        fake = FakeLinear([
            page([issue("ENG-2", created="2026-03-01T00:00:00Z")], True),
            page([issue("ENG-1", created="2026-01-01T00:00:00Z"), issue("ENG-3", created="2026-05-01T00:00:00Z")]),
        ])
        out = linear.LinearTracker(CFG, post=fake).list_candidates()
        self.assertEqual([t.key for t in out], ["ENG-1", "ENG-2", "ENG-3"])

    def test_transition_refuses_without_optin_label_or_with_hold(self):
        for labels, msg in ([["bug"], "lacks"], [["ShopFloor", "HOLD"], "hold"]):
            fake = FakeLinear([{"issue": issue("ENG-1", labels)}])
            with self.assertRaisesRegex(base.TrackerError, msg):
                linear.LinearTracker(CFG, post=fake).transition("ENG-1", "inProgress")
            self.assertEqual(len(fake.calls), 1)

    def test_transition_unknown_to_key(self):
        fake = FakeLinear([])
        with self.assertRaisesRegex(base.TrackerError, "done"):
            linear.LinearTracker(CFG, post=fake).transition("ENG-1", "done")

    def test_failed_or_null_mutation_payloads_raise(self):
        iss = {"issue": issue("ENG-1")}
        cases = [
            (lambda t: t.comment("ENG-1", "x"), {"commentCreate": {"success": True, "comment": None}}),
            (lambda t: t.comment("ENG-1", "x"), {"commentCreate": None}),
            (lambda t: t.update_comment("ENG-1", "C", "x"), {"commentUpdate": {"success": False}}),
            (lambda t: t.link_pr("ENG-1", "u"), {"attachmentLinkURL": {"success": False}}),
        ]
        for call, resp in cases:
            fake = FakeLinear(lambda q, v, resp=resp: iss if "issue(" in q else resp)
            with self.assertRaises(base.TrackerError):
                call(linear.LinearTracker(CFG, post=fake))
        fake = FakeLinear([{"issue": issue("ENG-1", ["shopfloor"])}, {"workflowStates": {"nodes": [{"id": "S"}]}}, {"issueUpdate": {"success": False}}])
        with self.assertRaises(base.TrackerError):
            linear.LinearTracker(CFG, post=fake).transition("ENG-1", "inProgress")

    def test_get_reports_labels_for_backoff(self):
        fake = FakeLinear([{"issue": issue("ENG-1", ["bug"])}])
        t = linear.LinearTracker(CFG, post=fake).get("ENG-1")
        self.assertEqual(t.labels, ["bug"])
        self.assertEqual(fake.calls[0][1], {"id": "ENG-1"})

    def test_comment_returns_id_and_update_comment(self):
        fake = FakeLinear([
            {"issue": issue("ENG-1")},
            {"commentCreate": {"success": True, "comment": {"id": "C1"}}},
            {"commentUpdate": {"success": True}},
        ])
        tr = linear.LinearTracker(CFG, post=fake)
        self.assertEqual(tr.comment("ENG-1", "hi"), "C1")
        self.assertEqual(fake.calls[1][1], {"input": {"issueId": "uuid-ENG-1", "body": "hi"}})
        tr.update_comment("ENG-1", "C1", "yo")
        self.assertEqual(fake.calls[2][1], {"id": "C1", "body": "yo"})

    def test_link_pr_already_attached_by_the_github_integration_is_done(self):
        url = "https://github.com/o/r/pull/1"
        fake = FakeLinear([{"issue": issue("ENG-1")}, {"attachmentLinkURL": {"success": False}},
                           {"issue": {"attachments": {"nodes": [{"url": url}]}}}])
        linear.LinearTracker(CFG, post=fake).link_pr("ENG-1", url)
        fake = FakeLinear([{"issue": issue("ENG-1")}, {"attachmentLinkURL": {"success": False}},
                           {"issue": {"attachments": {"nodes": [{"url": url + "0"}]}}}])
        with self.assertRaises(base.TrackerError):
            linear.LinearTracker(CFG, post=fake).link_pr("ENG-1", url)

    def test_link_pr_uses_attachment_link_url(self):
        fake = FakeLinear([{"issue": issue("ENG-1")}, {"attachmentLinkURL": {"success": True}}])
        linear.LinearTracker(CFG, post=fake).link_pr("ENG-1", "https://github.com/o/r/pull/1")
        self.assertIn("attachmentLinkURL", fake.calls[1][0])
        self.assertEqual(fake.calls[1][1], {"issueId": "uuid-ENG-1", "url": "https://github.com/o/r/pull/1"})

    def test_viewer(self):
        fake = FakeLinear([{"viewer": {"name": "Ann"}}])
        self.assertEqual(linear.LinearTracker(CFG, post=fake).viewer(), "Ann")

    def test_construct_with_fake_touches_no_filesystem(self):
        with mock.patch.object(linear.config, "read_secret", side_effect=AssertionError):
            linear.LinearTracker(CFG, post=FakeLinear([]))

    def test_auth_header_is_raw_key_and_mutations_do_not_retry(self):
        key = "lin_" + "api_" + "abc123"
        with mock.patch.object(linear.config, "read_secret", return_value=key), \
             mock.patch.object(linear, "request_json", return_value={"data": {"ok": 1}}) as rj:
            tr = linear.LinearTracker(CFG)
            tr.post("query { viewer { name } }", {})
            tr.post("mutation { issueUpdate }", {})
        (m, url), kw = rj.call_args_list[0]
        self.assertEqual((m, url), ("POST", "https://api.linear.app/graphql"))
        self.assertEqual(kw["headers"]["Authorization"], key)
        self.assertNotIn("retries", kw)
        self.assertEqual(rj.call_args_list[1][1]["retries"], 0)

    def test_graphql_errors_become_redacted_tracker_error(self):
        secret = "lin_" + "api_" + "A" * 30
        resp = {"errors": [{"message": "bad " + secret}]}
        with mock.patch.object(linear.config, "read_secret", return_value="k"), \
             mock.patch.object(linear, "request_json", return_value=resp):
            with self.assertRaises(base.TrackerError) as cm:
                linear.LinearTracker(CFG).post("query { x }", {})
        self.assertNotIn(secret, str(cm.exception))

    def test_http_error_becomes_tracker_error(self):
        from engine.lib.http import HttpError
        with mock.patch.object(linear.config, "read_secret", return_value="k"), \
             mock.patch.object(linear, "request_json", side_effect=HttpError(401, "nope")):
            with self.assertRaises(base.TrackerError):
                linear.LinearTracker(CFG).post("query { x }", {})

    def test_check_move_and_state_key(self):
        base.check_move(CFG, "todo", "inProgress")
        with self.assertRaisesRegex(base.TrackerError, r"^move todo->done is not in tracker.mayMove$"):
            base.check_move(CFG, "todo", "done")
        self.assertEqual(base.state_key(CFG, "In Review"), "review")
        self.assertIsNone(base.state_key(CFG, "Nope"))

    def test_team_check_reports_each_missing_piece(self):
        team = {"key": "ENG", "states": {"nodes": [{"name": "Todo"}, {"name": "In Progress"}]}}

        def check(labels, teams=(team,)):
            fake = FakeLinear([{"teams": {"nodes": list(teams)}, "issueLabels": {"nodes": labels}}])
            problems = make_tracker(CFG, post=fake).team_check()
            self.assertEqual(fake.calls[0][1], {"key": "ENG", "label": "shopfloor"})
            return problems

        missing_state = ["workflow state 'In Review' not found in team ENG"]
        self.assertEqual(check([{"name": "Shopfloor", "team": {"key": "ENG"}}]), missing_state)
        self.assertEqual(check([{"name": "shopfloor", "team": None}]), missing_state)  # workspace label
        self.assertIn("label 'shopfloor' not found in team ENG", check([{"name": "shopfloor", "team": {"key": "OPS"}}]))
        self.assertIn("label 'shopfloor' not found in team ENG", check([]))
        self.assertEqual(check([], teams=()), ["team 'ENG' not found"])

    def test_make_tracker(self):
        self.assertIsInstance(make_tracker(CFG, post=FakeLinear([])), linear.LinearTracker)


if __name__ == "__main__":
    unittest.main()
