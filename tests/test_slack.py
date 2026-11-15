from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.adapters.chat import make_chat
from engine.adapters.chat.base import ChatError, Reply, ThreadRef
from engine.adapters.chat.slack import SlackChat
from engine.adapters.tracker.base import Ticket
from engine.lib.http import HttpError
from tests.fakes import FakeSlack

CFG = {
    "voice": {"name": "Shopfloor", "style": "plain"},
    "chat": {"type": "slack", "tokenFile": "/nonexistent/secret",
             "slack": {"channel": "C0123", "ownerUserId": "U0OWNER"}},
}
T = ThreadRef("C0123", "1700000000.000100")


def ticket():
    return Ticket(key="ENG-1", id="u", title="Fix it", body="", labels=[], state="Todo",
                  url="https://linear.app/x/ENG-1", assignee=None, relations=[], created_at="")


class SlackTest(unittest.TestCase):
    def test_open_thread_posts_parent_with_voice_username(self):
        fake = FakeSlack([{"ok": True, "ts": "1.5", "channel": "C0123"}])
        ref = SlackChat(CFG, call=fake).open_thread(ticket())
        self.assertEqual(ref, ThreadRef("C0123", "1.5"))
        method, p = fake.calls[0]
        self.assertEqual(method, "chat.postMessage")
        self.assertEqual((p["channel"], p["username"]), ("C0123", "Shopfloor"))
        for s in ("ENG-1", "Fix it", "https://linear.app/x/ENG-1"):
            self.assertIn(s, p["text"])

    def test_post_returns_ts_in_thread(self):
        fake = FakeSlack([{"ok": True, "ts": "2.0"}])
        self.assertEqual(SlackChat(CFG, call=fake).post(T, "hi"), "2.0")
        p = fake.calls[0][1]
        self.assertEqual((p["thread_ts"], p["text"], p["username"]), (T.ts, "hi", "Shopfloor"))

    def test_ask_formats_options_exactly(self):
        fake = FakeSlack([{"ok": True, "ts": "2.0"}])
        SlackChat(CFG, call=fake).ask(T, "Which approach?", ["A", "B"], 2)
        self.assertEqual(
            fake.calls[0][1]["text"],
            "Which approach?\n1. A\n2. B\nRecommended: 2. Reply with a number, or write your own answer.")

    def test_poll_only_owner_new_messages(self):
        def m(ts, user="U0OWNER", text="x", **kw):
            return dict(ts=ts, user=user, text=text, **kw)
        msgs = [
            m(T.ts, text="parent"),
            m("1700000001.000100", bot_id="B1"),
            m("1700000002.000100", user="U0OTHER"),
            m("1700000003.000100"),
            m("1700000010.000100", text="approve"),
            m("1700000011.000100", subtype="message_changed"),
        ]
        fake = FakeSlack([{"ok": True, "messages": msgs}])
        out = SlackChat(CFG, call=fake).poll({"ENG-1": T}, {"ENG-1": "1700000003.000100"})
        self.assertEqual(out, [Reply("ENG-1", "U0OWNER", "approve", "1700000010.000100")])
        self.assertEqual(fake.calls[0][1]["oldest"], "1700000003.000100")

    def test_poll_compares_ts_numerically_and_paginates(self):
        pages = [
            {"ok": True, "messages": [{"ts": "1700000009.000100", "user": "U0OWNER", "text": "old"}],
             "response_metadata": {"next_cursor": "c2"}},
            {"ok": True, "messages": [{"ts": "1700000100.000100", "user": "U0OWNER", "text": "new"}]},
        ]
        fake = FakeSlack(pages)
        out = SlackChat(CFG, call=fake).poll({"ENG-1": T}, {"ENG-1": "999999999.000100"})
        self.assertEqual([r.text for r in out], ["old", "new"])
        self.assertEqual(fake.calls[1][1]["cursor"], "c2")
        # 9-digit since must not outrank 10-digit ts lexically
        self.assertEqual(SlackChat(CFG, call=FakeSlack(
            [{"ok": True, "messages": [{"ts": "1700000009.000100", "user": "U0OWNER", "text": "a"}]}])
        ).poll({"ENG-1": T}, {"ENG-1": "999999999.000100"})[0].text, "a")

    def test_poll_defaults_oldest_to_thread_ts(self):
        fake = FakeSlack([{"ok": True, "messages": []}])
        SlackChat(CFG, call=fake).poll({"ENG-1": T}, {})
        self.assertEqual(fake.calls[0][1]["oldest"], T.ts)

    def test_auth_test_returns_user(self):
        fake = FakeSlack([{"ok": True, "user": "shopbot"}])
        self.assertEqual(SlackChat(CFG, call=fake).auth_test(), "shopbot")

    def test_threadref_roundtrip(self):
        self.assertEqual(ThreadRef.from_dict(T.to_dict()), T)

    def test_make_chat(self):
        self.assertIsInstance(make_chat(CFG), SlackChat)


class DefaultCallTest(unittest.TestCase):
    def _chat(self):
        d = tempfile.mkdtemp()
        f = Path(d) / "tok"
        f.write_text("xoxb-" + "abc123")
        f.chmod(0o600)
        return SlackChat({**CFG, "chat": {**CFG["chat"], "tokenFile": str(f)}})

    def test_ok_false_raises_with_code(self):
        with mock.patch("engine.adapters.chat.slack.request_json", return_value={"ok": False, "error": "channel_not_found"}):
            with self.assertRaisesRegex(ChatError, "channel_not_found"):
                self._chat().auth_test()

    def test_http_error_wrapped_and_redacted(self):
        tok = "xoxb-" + "abc123"
        with mock.patch("engine.adapters.chat.slack.request_json", side_effect=HttpError(500, "bad " + tok)):
            with self.assertRaises(ChatError) as cm:
                self._chat().auth_test()
        self.assertNotIn(tok, str(cm.exception))

    def test_post_message_never_retried_and_bearer_header(self):
        with mock.patch("engine.adapters.chat.slack.request_json", return_value={"ok": True, "ts": "1", "user": "u"}) as rj:
            c = self._chat()
            c.post(T, "hi")
            c.auth_test()
        post, read = rj.call_args_list
        self.assertEqual(post.kwargs["retries"], 0)
        self.assertNotIn("retries", read.kwargs)
        self.assertEqual(post.kwargs["headers"]["Authorization"], "Bearer xoxb-abc123")
        self.assertEqual(post.args[:2], ("POST", "https://slack.com/api/chat.postMessage"))


if __name__ == "__main__":
    unittest.main()
