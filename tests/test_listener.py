from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from board import listener
from engine.adapters.chat.slack import SlackChat
from engine.lib import paths, state
from engine.lib.commands import CommandQueue
from tests.fakes import FakeSlack

CREW = {
    "id": "demo", "tracker": {"type": "linear", "tokenFile": "/x"},
    "chat": {"type": "slack", "tokenFile": "/x", "slack": {"channel": "C1", "ownerUserId": "U1"}},
    "repos": {"app": {"github": "o/app", "base": "main", "path": "/tmp/app"}},
}
THREAD = {"channel": "C1", "ts": "100.000"}


class ListenerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"SHOPFLOOR_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        (Path(tmp.name) / "crews").mkdir()
        (Path(tmp.name) / "crews" / "demo.json").write_text(json.dumps(CREW))
        self.asked = []

        def slack(method, params):
            self.asked.append(params["ts"])
            return {"ok": True, "messages": [{"user": "U1", "text": "approve", "ts": "101.000"}]}

        self.fake = FakeSlack(slack)
        p = mock.patch.object(listener, "make_chat", lambda cfg: SlackChat(cfg, call=self.fake))
        p.start()
        self.addCleanup(p.stop)

    def pending(self):
        return CommandQueue(paths.crew_state_dir("demo")).pending()

    def test_listener_queues_once_across_restarts(self):
        state.save_ticket("demo", "ENG-1", stage="needs_you", thread=THREAD)
        self.assertEqual(listener.poll_once(), 1)
        self.assertEqual(listener.poll_once(), 0)
        cursors = paths.crew_state_dir("demo") / "listener.json"
        self.assertEqual(json.loads(cursors.read_text()), {"ENG-1": "101.000"})
        cursors.unlink()  # lost cursor: the queue's (source, ts) dedupe still holds
        self.assertEqual(listener.poll_once(), 0)
        rows = self.pending()
        self.assertEqual([(r["key"], r["source"], r["ts"], r["cmd"]) for r in rows],
                         [("ENG-1", "slack", "101.000", "approve")])

    def test_listener_ignores_closed_tickets(self):
        state.save_ticket("demo", "ENG-1", stage="done", thread=THREAD)
        state.save_ticket("demo", "ENG-2", stage="building")
        self.assertEqual(listener.poll_once(), 0)
        self.assertEqual(self.fake.calls, [])

    def test_one_crew_failing_does_not_stop_others(self):
        (Path(os.environ["SHOPFLOOR_HOME"]) / "crews" / "aaa.json").write_text("{")
        state.save_ticket("demo", "ENG-1", stage="needs_you", thread=THREAD)
        self.assertEqual(listener.poll_once(), 1)


if __name__ == "__main__":
    unittest.main()
