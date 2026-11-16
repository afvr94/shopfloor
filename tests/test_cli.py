from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cli import init, install
from cli import main as cli_main
from engine.lib import config, paths, runner, state

FAKE_BIN = str(Path(__file__).resolve().parent / "bin")
CREW = {
    "id": "demo",
    "repos": {"app": {"github": "owner/app", "base": "main", "path": "/x/app"}},
    "tracker": {"type": "linear", "tokenFile": "/x/t",
                "linear": {"team": "ENG", "label": "shopfloor",
                           "states": {"todo": "Todo", "inProgress": "In Progress", "review": "In Review"}}},
    "chat": {"type": "slack", "tokenFile": "/x/s", "slack": {"channel": "C1", "ownerUserId": "U1"}},
    "schedule": {"start": "07:00", "everyHours": 3, "until": "22:00"},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = {"SHOPFLOOR_HOME": self.tmp + "/sf", "HOME": self.tmp, "FAKE_CLAUDE_SLEEP": "0",
               "PATH": FAKE_BIN + os.pathsep + os.environ["PATH"]}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def write_crew(self, cfg=CREW):
        f = paths.crew_config_path(cfg["id"])
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cfg))

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli_main.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def runs(self):
        return [r["result"] for r in state.recent_runs("demo")]


class Run(Base):
    def setUp(self):
        super().setUp()
        self.write_crew()

    def test_run_skips_when_locked(self):
        self.assertTrue(state.acquire_lock(paths.tick_lock("demo")))
        self.assertEqual(self.main("run", "demo")[0], 0)
        self.assertEqual(self.runs(), ["skipped: locked"])

    def test_run_finishes_and_releases_lock(self):
        self.assertEqual(self.main("run", "demo")[0], 0)
        self.assertEqual(self.runs(), ["tick finished"])
        self.assertFalse(paths.tick_lock("demo").exists())

    def test_run_logs_failure_exit_code(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_EXIT": "3"}):
            self.assertEqual(self.main("run", "demo")[0], 1)
        self.assertEqual(self.runs(), ["tick failed (exit 3)"])

    def test_run_releases_lock_after_timeout(self):
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_SLEEP": "30"}), \
             mock.patch.object(runner, "KILL_GRACE_SECS", 1):
            t0 = time.time()
            code = cli_main.run_tick("demo", timeout=0.3)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(code, 1)
        self.assertEqual(self.runs(), ["tick timed out"])
        self.assertFalse(paths.tick_lock("demo").exists())

    def test_run_uses_crew_cwd_and_logs_start_failure(self):
        seen = {}

        def popen(argv, **kw):
            seen.update(kw)
            raise OSError("no such file")
        cli_main.run_tick("demo", popen=popen)
        self.assertEqual(seen["cwd"], runner.claude_cwd("demo"))
        self.assertTrue(seen["start_new_session"])
        self.assertEqual(self.runs(), ["tick failed (could not start claude: no such file)"])
        self.assertFalse(paths.tick_lock("demo").exists())

    def test_sigterm_kills_child_and_releases_lock(self):
        import signal
        import subprocess
        env = {**os.environ, "FAKE_CLAUDE_SLEEP": "30", "FAKE_CLAUDE_OUT": self.tmp + "/fake"}
        p = subprocess.Popen([str(Path(cli_main.__file__).resolve().parent.parent / "bin" / "shopfloor"), "run", "demo"],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pidfile = Path(self.tmp + "/fake.pid")
        for _ in range(100):
            if pidfile.exists() and paths.tick_lock("demo").exists():
                break
            time.sleep(0.1)
        child = int(pidfile.read_text())
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        time.sleep(0.2)
        self.assertFalse(paths.tick_lock("demo").exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_unknown_subcommand_exits_2(self):
        self.assertEqual(self.main("bogus")[0], 2)


class Status(Base):
    def test_status_prints_one_line_per_crew(self):
        self.write_crew()
        self.write_crew({**CREW, "id": "second", "dryRun": False, "paused": True})
        paths.crew_config_path("broken").write_text("{nope")
        state.save_ticket("demo", "ENG-1", title="t", stage="needs_you")
        _, out, _ = self.main("status")
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 3, out)
        by = {l.split(":")[0]: l for l in lines}
        self.assertIn("dry run", by["demo"])
        self.assertIn("1 needs_you", by["demo"])
        self.assertIn("live", by["second"])
        self.assertIn("paused", by["second"])
        self.assertIn("error", by["broken"])
        self.assertEqual(self.main("status", "demo")[1].count("\n"), 1)


class Install(Base):
    def test_install_and_uninstall_touch_only_shopfloor_units(self):
        self.write_crew()
        other = Path(self.tmp) / ".config/systemd/user/other.service"
        other.parent.mkdir(parents=True)
        other.write_text("x")
        with mock.patch.object(install, "_activate", lambda *a: None):
            self.assertEqual(self.main("install")[0], 0)
            self.assertGreaterEqual(len(install.unit_paths()), 2)
            self.assertTrue((paths.crew_dir("demo") / "worktrees").is_dir())
            self.assertTrue((paths.crew_dir("demo") / "tickets").is_dir())
            with mock.patch.object(install.proc, "run"):
                self.assertEqual(self.main("uninstall")[0], 0)
        self.assertEqual(install.unit_paths(), [])
        self.assertTrue(other.exists())
        self.assertTrue(paths.crew_config_path("demo").exists())

    def test_board_runs_serve_in_foreground(self):
        with mock.patch.object(cli_main, "serve") as serve:
            self.assertEqual(self.main("board")[0], 0)
        serve.assert_called_once_with(config.load_board())


class Init(Base):
    ANSWERS = {"crew id": "demo", "linear team key": "ENG", "slack channel id": "C1", "slack owner user id": "U1",
               "repo name": "app", "github repo": "owner/app", "local path": "/x/app"}

    def ask(self, prompt):
        return next((v for k, v in self.ANSWERS.items() if k in prompt.lower()), "")

    def wizard(self, installer, doctor_rows=None):
        out = []
        secret = mock.Mock(side_effect=["lin_" + "TOK", "xoxb-" + "TOK"])
        rows = doctor_rows if doctor_rows is not None else [(True, "x", "")]
        with mock.patch.object(init.doctor, "check_all", return_value=rows):
            code = init.run_wizard(ask=self.ask, out=out.append, secret=secret, installer=installer)
        return code, "\n".join(out)

    @staticmethod
    def installer():
        return SimpleNamespace(install_board=mock.Mock(), install_crew=mock.Mock())

    def test_init_writes_config_and_0400_secrets_in_a_locked_folder(self):
        inst = self.installer()
        code, out = self.wizard(inst)
        self.assertEqual(code, 0)
        raw = paths.crew_config_path("demo").read_text()
        self.assertEqual(config.validate(json.loads(raw)), [])
        cfg = config.load_crew("demo")
        self.assertIs(cfg["dryRun"], True)
        for key in ("tracker", "chat"):
            f = Path(cfg[key]["tokenFile"])
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode), 0o500)
            self.assertNotIn(f.read_text(), raw)
        self.assertEqual(json.loads((paths.shopfloor_home() / "board.json").read_text())["crews"], ["demo"])
        inst.install_board.assert_called_once()
        inst.install_crew.assert_called_once()
        for doc in ("docs/setup-linear.md", "docs/setup-slack.md", "examples/slack-app-manifest.json"):
            self.assertIn(doc, out)
        self.assertNotIn("TOK", out)

    def test_init_tightens_existing_secret_and_refuses_to_overwrite_crew(self):
        sec = paths.shopfloor_home() / "secrets" / "demo-linear"
        sec.parent.mkdir(parents=True)
        sec.write_text("old")
        os.chmod(sec, 0o644)
        self.assertEqual(self.wizard(self.installer())[0], 0)
        self.assertEqual(stat.S_IMODE(sec.stat().st_mode), 0o400)
        self.assertEqual(sec.read_text().strip(), "lin_TOK")
        again = self.installer()
        self.assertEqual(self.wizard(again)[0], 1)
        again.install_crew.assert_not_called()

    def test_init_aborts_after_three_empty_secrets(self):
        out = []
        secret = mock.Mock(return_value="  ")
        with self.assertRaises(ValueError):
            init.run_wizard(ask=self.ask, out=out.append, secret=secret, installer=self.installer())
        self.assertEqual(secret.call_count, 3)
        self.assertFalse(paths.crew_config_path("demo").exists())

    def test_init_skips_install_when_doctor_fails(self):
        inst = self.installer()
        code, out = self.wizard(inst, doctor_rows=[(False, "gh auth", "not logged in")])
        self.assertEqual(code, 1)
        inst.install_crew.assert_not_called()
        self.assertIn("gh auth", out)


if __name__ == "__main__":
    unittest.main()
