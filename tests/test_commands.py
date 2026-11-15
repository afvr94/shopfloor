from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.lib.commands import COMMANDS, CommandQueue, parse


class ParseTest(unittest.TestCase):
    def test_parse_commands(self):
        self.assertEqual(parse("Approve"), {"cmd": "approve", "arg": ""})
        self.assertEqual(parse("adjust use the v2 endpoint")["arg"], "use the v2 endpoint")
        self.assertEqual(parse("/lesson always run lint"), {"cmd": "lesson", "arg": "always run lint"})
        self.assertEqual(parse("!go"), {"cmd": "go", "arg": ""})
        self.assertEqual(parse("2"), {"cmd": "answer", "arg": "2", "choice": 2})
        self.assertEqual(parse("sounds good but rename it")["cmd"], "answer")
        self.assertEqual(parse("   "), {"cmd": "answer", "arg": ""})
        self.assertNotIn("choice", parse("10"))
        self.assertNotIn("choice", parse("0"))
        self.assertEqual(len(COMMANDS), 9)


class QueueTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
