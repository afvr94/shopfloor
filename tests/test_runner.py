from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine.lib import config, paths, runner, state

FAKE_BIN = str(Path(__file__).resolve().parent / "bin")


def cfg(max_parallel=2, minutes=180):
    return {"id": "demo", "limits": {"maxParallel": max_parallel, "buildMaxMinutes": minutes}}


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        env = {"SHOPFLOOR_HOME": self.home, "PATH": FAKE_BIN + os.pathsep + os.environ["PATH"],
               "FAKE_CLAUDE_OUT": os.path.join(self.home, "fake")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = cfg()
        c = mock.patch.object(runner.config, "load_crew", side_effect=lambda _id: self.cfg)
        c.start()
        self.addCleanup(c.stop)

    def results(self):
        return [r["result"] for r in state.recent_runs("demo")]

    def lock(self):
        return paths.crew_state_dir("demo") / "build.lock"

    def test_invocation_clears_nested_session_vars_and_adds_sf_to_path(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "x",
                                          "CLAUDE_CODE_SSE_PORT": "9"}):
            argv, env = runner.claude_invocation("demo", "hello")
        self.assertEqual(argv[:3], ["claude", "-p", "hello"])
        self.assertEqual(argv[3:11], ["--plugin-dir", str(paths.ENGINE_DIR), "--permission-mode",
                                      "bypassPermissions", "--strict-mcp-config", "--setting-sources", "",
                                      "--settings"])
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
            self.assertNotIn(k, env)
        self.assertEqual(env["SHOPFLOOR_CREW"], "demo")
        self.assertEqual(env["SHOPFLOOR_HOME"], self.home)
        self.assertTrue(env["PATH"].startswith(str(paths.SF_BIN) + os.pathsep))
        for k, v in {"GIT_EDITOR": "true", "EDITOR": "true", "VISUAL": "true", "GIT_SEQUENCE_EDITOR": "true",
                     "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}.items():
            self.assertEqual(env[k], v)

    def test_sandbox_settings_json_is_pinned(self):
        argv, _ = runner.claude_invocation("demo", "p")
        self.cfg = {**cfg(), "guard": {"secretFiles": ["~/keys/deploy.pem"], "offLimits": ["/srv/private"]},
                    "tracker": {"tokenFile": "/s/linear"}}
        argv, _ = runner.claude_invocation("demo", "p")
        home = os.path.expanduser("~")
        deny = [p.replace("~", home, 1) for p in config.DEFAULT_SECRET_FILES] + \
            [home + "/keys/deploy.pem", "/srv/private", os.path.join(self.home, "secrets"), "/s/linear"]
        want = {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                            "excludedCommands": ["sf *"],
                            "filesystem": {"allowWrite": [str(paths.worktrees_dir("demo")),
                                                          str(paths.crew_state_dir("demo") / "tickets"),
                                                          tempfile.gettempdir()],
                                           "denyRead": deny},
                            "network": {"allowLocalBinding": False,
                                        "deniedDomains": ["localhost", "*.localhost", "127.0.0.1", "0.0.0.0",
                                                          "[::1]", "[::ffff:127.0.0.1]", "[::ffff:7f00:1]",
                                                          "[::]", "[::ffff:0.0.0.0]"]}}}
        self.assertEqual(json.loads(argv[-1]), want)
        self.assertFalse(any(w.startswith(str(paths.ticket_state_root("demo"))) for w in
                             json.loads(argv[-1])["sandbox"]["filesystem"]["allowWrite"]))
        for p in ("~/.ssh", "~/.config/gh", "~/.claude.json"):
            self.assertIn(os.path.expanduser(p), deny)

    def test_build_passes_slash_command_to_claude(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_SLEEP": "0"}):
            runner.run_builds("demo", ["ENG-1"])
        argv = Path(self.home, "fake.argv").read_text().splitlines()
        self.assertEqual(argv[:3], ["-p", "/shopfloor:build ENG-1", "--plugin-dir"])

    def test_build_runs_in_worktrees_dir(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_SLEEP": "0", "FAKE_CLAUDE_CWD": "1"}):
            runner.run_builds("demo", ["ENG-1"])
        self.assertEqual(Path(self.home, "fake.cwd").read_text().strip(),
                         str(paths.worktrees_dir("demo").resolve()))

    def test_missing_claude_logs_failure_and_other_keys_finish(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        with mock.patch.dict(os.environ, {"PATH": empty}):
            runner.run_builds("demo", ["ENG-1", "ENG-2"])
        res = self.results()
        self.assertEqual(len(res), 2)
        self.assertTrue(all(r.startswith("build failed (could not start claude:") for r in res), res)
        self.assertFalse(self.lock().exists())

    def test_timeout_logged_when_group_already_gone(self):
        self.cfg = cfg(minutes=0.01)
        real = os.killpg

        def kill_then_vanish(pid, sig):
            real(pid, sig)
            raise ProcessLookupError

        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_SLEEP": "30"}), \
                mock.patch.object(runner.os, "killpg", kill_then_vanish):
            runner.run_builds("demo", ["ENG-1"])
        self.assertEqual(self.results(), ["build timed out"])

    def test_runs_up_to_max_parallel_at_once(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_SLEEP": "0.5"}):
            t = time.monotonic()
            runner.run_builds("demo", ["ENG-1", "ENG-2", "ENG-3"])
            wall = time.monotonic() - t
        self.assertTrue(0.9 <= wall <= 1.4, wall)
        self.assertEqual(self.results(), ["build finished"] * 3)
        self.assertEqual(len(list(paths.logs_dir().glob("build-demo-ENG-*-*.log"))), 3)

    def test_timeout_kills_process_group(self):
        self.cfg = cfg(minutes=0.01)
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_SLEEP": "30"}):
            t = time.monotonic()
            runner.run_builds("demo", ["ENG-1"])
        self.assertLess(time.monotonic() - t, 5)
        self.assertEqual(self.results(), ["build timed out"])
        pid = int(Path(self.home, "fake.pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_busy_when_lock_held(self):
        self.assertTrue(state.acquire_lock(self.lock()))
        with mock.patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaises(runner.RunnerBusy):
                runner.start("demo", ["ENG-1"])
        popen.assert_not_called()
        self.assertEqual(runner.status("demo")["pid"], os.getpid())

    def test_start_detaches_runner(self):
        with mock.patch.object(runner.subprocess, "Popen") as popen:
            popen.return_value.pid = 4242
            self.assertEqual(runner.start("demo", ["ENG-1", "ENG-2"]), 4242)
        args, kw = popen.call_args
        self.assertEqual(args[0][1:], ["-m", "engine.lib.runner", "demo", "ENG-1", "ENG-2"])
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(kw["cwd"], paths.REPO_ROOT)
        self.assertIsNone(runner.status("demo"))

    def test_lock_released_after_crash(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_EXIT": "1"}):
            runner.run_builds("demo", ["ENG-1"])
        self.assertEqual(self.results(), ["build failed (exit 1)"])
        self.assertFalse(self.lock().exists())


if __name__ == "__main__":
    unittest.main()
