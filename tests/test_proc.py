from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertIn("timed out after", str(cm.exception))

    def test_timeout_kills_the_whole_process_group(self):
        t = time.monotonic()
        with self.assertRaises(proc.ProcError):
            proc.run(["sh", "-c", "sleep 30 & sleep 30 & wait"], timeout=0.3)
        self.assertLess(time.monotonic() - t, 5)

    def test_input_goes_to_stdin(self):
        self.assertEqual(proc.run(["cat"], input="body text"), "body text")

    def test_timeout_does_not_hang_on_a_child_that_left_the_group(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "pid"
            escape = (f"import os, time; os.setsid(); open({str(pidfile)!r}, 'w').write(str(os.getpid())); "
                      "time.sleep(30)")
            t = time.monotonic()
            with mock.patch.object(proc, "DRAIN_TIMEOUT", 0.3), self.assertRaises(proc.ProcError):
                proc.run(["sh", "-c", f"{sys.executable} -c \"{escape}\" & wait"], timeout=0.5)
            self.assertLess(time.monotonic() - t, 5)
            for _ in range(50):
                if pidfile.exists() and pidfile.read_text():
                    break
                time.sleep(0.05)
            os.kill(int(pidfile.read_text()), signal.SIGKILL)

    def test_an_interrupt_kills_the_process_group(self):
        started = []

        class Interrupted(subprocess.Popen):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                started.append(self)

            def communicate(self, input=None, timeout=None):
                raise KeyboardInterrupt

        with mock.patch.object(proc.subprocess, "Popen", Interrupted), self.assertRaises(KeyboardInterrupt):
            proc.run(["sleep", "30"])
        self.assertEqual(started[0].poll(), -signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
