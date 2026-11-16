from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from engine.lib.commands import COMMANDS, CommandQueue, CommandQueueError, parse


class ParseTest(unittest.TestCase):
    def test_parse_commands(self):
        self.assertEqual(parse("Approve"), {"cmd": "approve", "arg": ""})
        self.assertEqual(parse("adjust use the v2 endpoint")["arg"], "use the v2 endpoint")
        self.assertEqual(parse("/lesson always run lint"), {"cmd": "lesson", "arg": "always run lint"})
        self.assertEqual(parse("!go"), {"cmd": "go", "arg": ""})
        self.assertEqual(parse("2"), {"cmd": "answer", "arg": "2", "choice": 2})
        self.assertEqual(parse("sounds good but rename it")["cmd"], "answer")
        self.assertEqual(parse("adjust\nuse v2"), {"cmd": "adjust", "arg": "use v2"})
        self.assertEqual(parse("   "), {"cmd": "answer", "arg": ""})
        self.assertNotIn("choice", parse("10"))
        self.assertNotIn("choice", parse("0"))
        self.assertEqual(len(COMMANDS), 9)


class QueueTest(unittest.TestCase):
    def test_ack_is_cumulative(self):
        q = CommandQueue(Path(tempfile.mkdtemp()))
        for i in range(1, 5):
            q.append("ENG-1", "slack", f"answer {i}", f"{i}.0")
        q.ack(3)
        self.assertEqual([r["seq"] for r in q.pending()], [4])

    def test_queue_dedupes_and_acks(self):
        d = Path(tempfile.mkdtemp())
        q = CommandQueue(d)
        self.assertTrue(q.append("ENG-1", "slack", "approve", "1.0"))
        self.assertFalse(q.append("ENG-1", "slack", "approve", "1.0"))
        self.assertTrue(q.append(None, "slack", "2", "2.0"))
        p = q.pending()
        self.assertEqual([c["seq"] for c in p], [1, 2])
        self.assertEqual(p[0]["cmd"], "approve")
        self.assertEqual(p[1]["choice"], 2)
        self.assertIsNone(p[1]["key"])
        q.ack(1)
        q2 = CommandQueue(d)
        self.assertEqual([c["seq"] for c in q2.pending()], [2])
        self.assertFalse(q2.append("ENG-1", "slack", "approve", "1.0"))
        self.assertTrue(q2.append("ENG-1", "slack", "go", "3.0"))
        self.assertEqual([c["seq"] for c in q2.pending()], [2, 3])

    def _run(self, d, jobs):
        res = []
        ts = [threading.Thread(target=lambda j=j: res.append(CommandQueue(d).append(*j))) for j in jobs]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return res

    def test_threads_same_ts_one_row(self):
        d = Path(tempfile.mkdtemp())
        res = self._run(d, [("ENG-1", "slack", "approve", "1.0")] * 20)
        self.assertEqual(res.count(True), 1)
        self.assertEqual(len(CommandQueue(d).pending()), 1)

    def test_threads_distinct_ts_unique_seqs(self):
        d = Path(tempfile.mkdtemp())
        self._run(d, [("ENG-1", "slack", "go", f"{i}.0") for i in range(20)])
        seqs = [c["seq"] for c in CommandQueue(d).pending()]
        self.assertEqual(sorted(seqs), list(range(1, 21)))

    def test_torn_line_skipped_and_appends_work(self):
        d = Path(tempfile.mkdtemp())
        q = CommandQueue(d)
        q.append("K", "slack", "go", "1.0")
        with open(q.log, "a") as f:
            f.write('{"seq": 2, "ke')
        self.assertEqual([c["seq"] for c in q.pending()], [1])
        self.assertTrue(q.append("K", "slack", "go", "2.0"))
        self.assertEqual([c["seq"] for c in q.pending()], [1, 2])

    def test_corrupt_cursor_raises(self):
        d = Path(tempfile.mkdtemp())
        q = CommandQueue(d)
        q.append("K", "slack", "go", "1.0")
        q.cursor.write_text("{oops")
        with self.assertRaises(CommandQueueError):
            q.pending()
        with self.assertRaises(CommandQueueError):
            q.ack(1)


if __name__ == "__main__":
    unittest.main()
