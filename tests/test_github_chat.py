from __future__ import annotations

import json
import unittest

from engine.adapters.chat import make_chat
from engine.adapters.chat.base import ChatError, Reply, ThreadRef
from engine.adapters.chat.github import GitHubChat
from engine.adapters.tracker.base import Ticket
from engine.adapters.tracker.github import MARKER
from engine.lib.proc import ProcError
from tests.fakes import FakeGh

CFG = {
    "tracker": {"type": "github", "github": {"repo": "acme/app"}},
    "chat": {"type": "github", "github": {"owner": "Ana"}},
    "voice": {"name": "Shopfloor"},
}
TICKET = Ticket(key="GH-7", id="n7", title="t", body="", labels=[], state="todo",
                url="https://github.com/acme/app/issues/7", assignee=None, relations=[],
                created_at="2026-01-01T00:00:00Z", closed=False)
TOKEN = "gh" + "p_abc123"
TH = ThreadRef("acme/app#7", "9")


def chat(answers):
    fake = FakeGh(answers)
    return make_chat(CFG, run=fake), fake


def comment(id, user, body):
    return json.dumps({"id": id, "user": {"login": user}, "body": body})


class GitHubChatTests(unittest.TestCase):
    def test_make_chat_picks_github(self):
        self.assertIsInstance(make_chat(CFG, run=FakeGh([])), GitHubChat)

    def test_open_thread_floor_is_newest_comment(self):
        c, fake = chat(["5\n12\n9\n"])
        self.assertEqual(c.open_thread(TICKET), ThreadRef("acme/app#7", "12"))
        self.assertEqual(fake.calls[0], (["gh", "api", "--paginate", "repos/acme/app/issues/7/comments",
                                          "--jq", ".[].id"], None))
        c, _ = chat([""])
        self.assertEqual(c.open_thread(TICKET).ts, "0")

    def test_post_appends_marker_on_stdin(self):
        c, fake = chat(["991\n"])
        self.assertEqual(c.post(TH, "hi"), "991")
        argv, stdin = fake.calls[0]
        self.assertEqual(argv, ["gh", "api", "-X", "POST", "repos/acme/app/issues/7/comments",
                                "--input", "-", "--jq", ".id"])
        self.assertEqual(stdin, json.dumps({"body": "hi\n\n— Shopfloor\n\n" + MARKER}))
        self.assertFalse(any("hi" in a for a in argv))

    def test_ask_format(self):
        c, fake = chat(["1\n"])
        c.ask(TH, "Which?", ["a", "b"], 2)
        body = json.loads(fake.calls[0][1])["body"]
        self.assertTrue(body.startswith(
            "Which?\n1. a\n2. b\nRecommended: 2. Reply with a number, or write your own answer."))

    def test_poll_filters_and_orders(self):
        out = "\n".join([comment(13, "ana", "2"), comment(8, "ana", "old"), comment(10, "ANA", "approve"),
                         comment(11, "ana", "bot\n\n" + MARKER), comment(12, "mallory", "approve")]) + "\n"
        c, fake = chat([out])
        self.assertEqual(c.poll({"GH-7": TH}, {"GH-7": "9"}),
                         [Reply("GH-7", "Ana", "approve", "10"), Reply("GH-7", "Ana", "2", "13")])
        self.assertEqual(fake.calls[0][0], ["gh", "api", "--paginate", "repos/acme/app/issues/7/comments",
                                            "--jq", ".[]"])

    def test_poll_counts_owner_text_that_only_quotes_the_marker(self):
        out = "\n".join([comment(10, "ana", "about " + MARKER + " here"), comment(11, "ana", "bot\n\n" + MARKER + "\n")])
        c, _ = chat([out + "\n"])
        self.assertEqual(c.poll({"GH-7": TH}, {}), [Reply("GH-7", "Ana", "about " + MARKER + " here", "10")])

    def test_post_signs_with_the_voice(self):
        c, fake = chat(["1\n"])
        make_chat({**CFG, "voice": {"name": "Rita"}}, run=fake).post(TH, "hi")
        self.assertEqual(json.loads(fake.calls[0][1])["body"], "hi\n\n— Rita, a Shopfloor crew\n\n" + MARKER)

    def test_thread_outside_the_repo_is_refused_without_calling_gh(self):
        for channel in ("other/repo#7", "acme/other#7", "acme/app-x#7", "x/acme/app#7", "acme/app#7/../x",
                        "acme/app#", "acme/app", "C0123"):
            th = ThreadRef(channel, "9")
            for name, call in (("post", lambda c: c.post(th, "hi")),
                               ("ask", lambda c: c.ask(th, "Which?", ["a"], 1)),
                               ("poll", lambda c: c.poll({"GH-7": th}, {}))):
                with self.subTest(channel=channel, call=name):
                    c, fake = chat([])
                    with self.assertRaisesRegex(ChatError, "is not an issue in acme/app"):
                        call(c)
                    self.assertEqual(fake.calls, [])

    def test_thread_repo_match_ignores_case(self):
        c, fake = chat(["1\n"])
        c.post(ThreadRef("ACME/App#7", "9"), "hi")
        self.assertEqual(fake.calls[0][0][4], "repos/acme/app/issues/7/comments")

    def test_poll_floor_defaults_to_thread_ts(self):
        c, _ = chat([comment(9, "ana", "x") + "\n" + comment(10, "ana", "y") + "\n"])
        self.assertEqual(c.poll({"GH-7": TH}, {}), [Reply("GH-7", "Ana", "y", "10")])

    def test_writes_not_retried_and_errors_redacted(self):
        c, fake = chat([ProcError(["gh"], 1, "HTTP 502 token " + TOKEN)])
        with self.assertRaises(ChatError) as cm:
            c.post(TH, "hi")
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_rate_limit_message(self):
        c, _ = chat([ProcError(["gh"], 1, "API rate limit exceeded")])
        with self.assertRaisesRegex(ChatError, "rate limit reached"):
            c.auth_test()

    def test_channel_check(self):
        c, fake = chat([json.dumps({"has_issues": False}), json.dumps({"has_issues": True})])
        self.assertEqual(c.channel_check(), "issues are turned off in acme/app")
        self.assertIsNone(c.channel_check())
        self.assertEqual(fake.calls[0][0], ["gh", "api", "repos/acme/app"])

    def test_auth_test(self):
        c, fake = chat(["ana\n"])
        self.assertEqual(c.auth_test(), "ana")
        self.assertEqual(fake.calls[0][0], ["gh", "api", "user", "--jq", ".login"])


if __name__ == "__main__":
    unittest.main()
