from __future__ import annotations

import unittest

from engine.lib import proc

TOKEN = "xox" + "b-" + "1" * 20


class Proc(unittest.TestCase):
    def test_returns_stdout(self):
        self.assertEqual(proc.run(["echo", "hi"]), "hi\n")

    def test_raises_with_redacted_stderr(self):
        with self.assertRaises(proc.ProcError) as cm:
            proc.run(["sh", "-c", f"echo {TOKEN} >&2; exit 3"])
        self.assertEqual(cm.exception.code, 3)
        self.assertNotIn(TOKEN, cm.exception.stderr)
        self.assertIn("[redacted]", cm.exception.stderr)

    def test_check_false_returns_stdout(self):
        self.assertEqual(proc.run(["sh", "-c", "echo out; exit 1"], check=False), "out\n")

    def test_timeout_raises(self):
        with self.assertRaises(proc.ProcError) as cm:
            proc.run(["sleep", "5"], timeout=0.2)
        self.assertIsNone(cm.exception.code)


if __name__ == "__main__":
    unittest.main()
