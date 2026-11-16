from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from cli import doctor
from engine.adapters.chat import make_chat
from engine.adapters.tracker import make_tracker
from engine.lib import config, paths, signing, state
from engine.lib.proc import ProcError
from engine.lib.proc import run as real_run
from tests.fakes import FakeLinear, FakeSlack

TOKEN = "lin_api_" + "SECRETVALUE123"
SLACK_TOKEN = "xoxb-" + "abc"
TEAM = {"key": "ENG", "labels": {"nodes": [{"name": "shopfloor"}]},
        "states": {"nodes": [{"name": n} for n in ("Todo", "In Progress", "In Review")]}}


def linear_answer(label=True):
    def answer(query, variables):
        if "viewer" in query:
            return {"viewer": {"name": "Bot"}}
        team = json.loads(json.dumps(TEAM))
        if not label:
            team["labels"]["nodes"] = []
        return {"teams": {"nodes": [team]}}
    return answer


def slack_answer(method, params):
    if method == "conversations.info":
        return {"ok": True, "channel": {"is_member": True}}
    return {"ok": True, "user": "bot"}


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"SHOPFLOOR_HOME": str(self.tmp / "sf")})
        p.start()
        self.addCleanup(p.stop)
        home = paths.shopfloor_home()
        (home / "crews").mkdir(parents=True)
        (home / "secrets").mkdir()
        self.secrets = {}
        for n, value in (("linear", TOKEN), ("slack", SLACK_TOKEN)):
            f = home / "secrets" / n
            fd = os.open(f, os.O_CREAT | os.O_WRONLY, 0o600)
            os.write(fd, value.encode())
            os.close(fd)
            self.secrets[n] = f
        self.repo = self.tmp / "app"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", "git@github.com:owner/app.git"],
                       check=True)
        cfg = {
            "id": "demo", "dryRun": True,
            "repos": {"app": {"github": "owner/app", "base": "main", "path": str(self.repo)}},
            "protectedBranches": ["main"],
            "tracker": {"type": "linear", "tokenFile": str(self.secrets["linear"]),
                        "linear": {"team": "ENG", "label": "shopfloor",
                                   "states": {"todo": "Todo", "inProgress": "In Progress", "review": "In Review"}},
                        "mayMove": ["todo->inProgress"]},
            "chat": {"type": "slack", "tokenFile": str(self.secrets["slack"]),
                     "slack": {"channel": "C1", "ownerUserId": "U1"}},
        }
        (home / "crews" / "demo.json").write_text(json.dumps(cfg))
        (paths.crew_dir("demo") / "tickets").mkdir(parents=True)
        (paths.crew_dir("demo") / "worktrees").mkdir()
        state.relock_all("demo")
        self.protected = True
        self.missing = set()
        self.invisible = self.api_error = self.redirect = False

    def deps(self, label=True):
        def run(argv, **kw):
            if argv[:2] == ["gh", "auth"]:
                return ""
            if argv[:2] == ["gh", "api"]:
                if self.invisible:
                    raise ProcError(argv, 1, "gh: Not Found (HTTP 404)")
                if argv[2] == "repos/owner/app":
                    return "{}"
                if self.api_error:
                    raise ProcError(argv, 1, "gh: Bad credentials (HTTP 401)")
                if "/branches/main" not in argv[2]:
                    if self.redirect:
                        return json.dumps({"name": "main", "protected": False})
                    raise ProcError(argv, 1, "gh: Branch not found (HTTP 404)")
                return json.dumps({"name": "main", "protected": self.protected})
            return real_run(argv, **kw)
        return {
            "which": lambda n: None if n in self.missing else "/usr/bin/" + n,
            "run": run,
            "tracker": lambda c: make_tracker(c, post=FakeLinear(linear_answer(label))),
            "chat": lambda c: make_chat(c, call=FakeSlack(slack_answer)),
            "health": lambda url: True,
            "lsremote": lambda cfg, name: None,
            "reach": lambda cid: None,
            "version": lambda: "2.1.284 (Claude Code)",
        }

    def rows(self, **kw):
        return doctor.check_all("demo", self.deps(**kw))

    def failing(self, rows):
        return [name for ok, name, _ in rows if not ok]

    def test_live_smoke_runs_only_with_live(self):
        calls = []
        d = {**self.deps(), "smoke": lambda cid: calls.append(cid) or "claude exited 1: no sandbox"}
        self.assertNotIn("live", " ".join(n for _, n, _ in doctor.check_all("demo", d)))
        self.assertEqual(calls, [])
        rows = doctor.check_all("demo", d, live=True)
        self.assertEqual(calls, ["demo"])
        self.assertEqual(self.failing(rows), ["demo: live sandbox session"])

    def test_live_checks_origin_with_ls_remote(self):
        seen = []
        d = {**self.deps(), "smoke": lambda cid: None,
             "lsremote": lambda cfg, name: seen.append((cfg["id"], name)) or "origin has no main branch"}
        self.assertNotIn("demo: git ls-remote app", [n for _, n, _ in doctor.check_all("demo", d)])
        rows = doctor.check_all("demo", d, live=True)
        self.assertEqual((self.failing(rows), seen), (["demo: git ls-remote app"], [("demo", "app")]))

    def test_ls_remote_runs_from_the_transport_repo(self):
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "set-url", "origin", str(origin)], check=True)
        cfg = config.load_crew("demo")
        self.assertEqual(doctor.ls_remote(cfg, "app"), "origin has no main branch")
        with mock.patch.object(doctor.gitsafe, "git", return_value="abc\trefs/heads/main") as g, \
                mock.patch.object(doctor.transport, "ensure", return_value=Path("/t")):
            self.assertIsNone(doctor.ls_remote(cfg, "app"))
        g.assert_called_once_with(Path("/t"), "ls-remote", "--heads", "--upload-pack=git-upload-pack", "origin",
                                  "main", bare=True)

    def test_live_smoke_checks_sandboxed_write_and_sf_write(self):
        def fake_run(argv, cwd, **kw):
            nonce = re.search(r"smoke-([0-9a-f]+)`", argv[2]).group(1)
            self.assertIn("--setting-sources", argv)
            Path(cwd, "smoke-ok").touch()
            state.log_run("demo", "SMOKE-1", f"smoke-{nonce}")
            return subprocess.CompletedProcess(argv, 0, "DONE", "")
        with mock.patch.object(doctor.subprocess, "run", fake_run):
            self.assertIsNone(doctor.live_smoke("demo"))
        with mock.patch.object(doctor.subprocess, "run",
                               lambda argv, cwd, **kw: subprocess.CompletedProcess(argv, 0, "DONE", "")):
            self.assertIn("sandboxed write", doctor.live_smoke("demo"))

    def test_board_reach_passes_only_when_the_probe_ran_and_nothing_arrived(self):
        def ran(proxy, rc):
            def run(argv, cwd, **kw):
                deny = json.loads(argv[-1])["sandbox"]["network"]["deniedDomains"]
                self.assertTrue({"127.0.0.1", "[::ffff:127.0.0.1]", "[::ffff:7f00:1]", "[::]"} <= set(deny))
                for form in ("[::ffff:127.0.0.1]", "[::ffff:7f00:1]", "CONNECT 2130706433:", "proxy=$HTTP_PROXY"):
                    self.assertIn(form, argv[2])
                Path(cwd, "reach.txt").write_text(f"proxy={proxy}\n" + f"rc={rc}\n" * 6)
                return subprocess.CompletedProcess(argv, 0, "DONE", "")
            return run

        def leaky(argv, cwd, **kw):
            url = re.search(r"http://127\.0\.0\.1:\d+/healthz", argv[2]).group(0)
            urllib.request.urlopen(url, timeout=5).close()
            return ran("http://localhost:9", 0)(argv, cwd)

        for run, want in ((ran("http://localhost:9", 7), None), (leaky, "crews could reach the board"),
                          (ran("", 7), "no $HTTP_PROXY"),
                          (lambda argv, cwd, **kw: subprocess.CompletedProcess(argv, 1, "", ""), "did not run")):
            with self.subTest(want=want), mock.patch.object(doctor.subprocess, "run", run):
                got = doctor.board_reach("demo")
                self.assertIsNone(got) if want is None else self.assertIn(want, got)

    def test_claude_older_than_the_floor_fails_with_an_upgrade_hint(self):
        for out, want in (("2.1.284 (Claude Code)", None), ("2.2.0 (Claude Code)", None), ("10.0.1", None),
                          ("2.1.283 (Claude Code)", "claude update"), ("1.9.999", "claude update"),
                          ("", "can't read a version")):
            with self.subTest(out=out):
                rows = doctor.check_all("demo", {**self.deps(), "version": lambda out=out: out})
                row = [r for r in rows if r[1] == "claude version"][0]
                self.assertEqual(row[0], want is None)
                if want:
                    self.assertIn(want, row[2])
        self.missing.add("claude")
        self.assertNotIn("claude version", [r[1] for r in self.rows()])

    def test_secrets_folder_and_token_modes(self):
        rows = {n: (ok, d) for ok, n, d in self.rows()}
        self.assertEqual(rows["secrets folder"], (True, ""))
        self.assertEqual(rows["demo: tracker secret"], (True, ""))
        os.chmod(self.secrets["linear"], 0o600)  # written by hand: passes, with a warning
        ok, detail = {n: (ok, d) for ok, n, d in self.rows()}["demo: tracker secret"]
        self.assertTrue(ok)
        self.assertIn("warning", detail)
        out = []
        doctor.report(self.rows(), out.append)
        self.assertTrue(any(line.startswith("✓ demo: tracker secret: warning") for line in out), out)
        os.chmod(state.secrets_dir(), 0o700)
        self.assertIn("secrets folder", self.failing(self.rows()))
        state.relock_all("demo")
        self.assertEqual(stat.S_IMODE(os.stat(state.secrets_dir()).st_mode), 0o500)
        self.assertEqual(stat.S_IMODE(os.stat(self.secrets["linear"]).st_mode), 0o400)
        self.assertNotIn("secrets folder", self.failing(self.rows()))

    def test_a_damaged_signing_key_fails_its_row(self):
        signing.mac("s", {})
        os.chmod(signing.key_path().parent, 0o700)
        self.assertEqual([n for n in self.failing(self.rows()) if n == "signing key"], ["signing key"])

    def test_guard_self_test_checks_protected_branch_even_in_dry_run(self):
        seen = []
        real = doctor.decide
        with mock.patch.object(doctor, "decide", lambda *a: seen.append(a[3]) or real(*a)):
            self.rows()
        self.assertEqual([c["dryRun"] for c in seen], [False])

    def test_all_green_with_fakes(self):
        rows = self.rows()
        self.assertEqual(self.failing(rows), [], rows)
        names = " ".join(n for _, n, _ in rows)
        for expect in ("platform", "python", "claude", "gh auth", "branch protection main", "guard", "sandbox"):
            self.assertIn(expect, names)

    def test_reports_each_failure_without_secrets(self):
        self.missing.add("gh")
        os.chmod(self.secrets["slack"], 0o644)
        rows = self.rows(label=False)
        bad = " | ".join(f"{n}: {d}" for ok, n, d in rows if not ok)
        self.assertGreaterEqual(len(self.failing(rows)), 3)
        self.assertRegex(bad, "gh")
        self.assertRegex(bad, "0600")
        self.assertRegex(bad, "label 'shopfloor'")
        self.assertNotIn(TOKEN, str(rows))
        self.assertNotIn(SLACK_TOKEN, str(rows))

    def test_unprotected_branch_fails_with_how_to_fix(self):
        self.protected = False
        (row,) = [r for r in self.rows() if not r[0]]
        self.assertIn("branch protection main", row[1])
        self.assertIn("Settings", row[2])

    def test_branch_missing_on_remote_is_skipped(self):
        cfg_path = paths.crew_config_path("demo")
        cfg = json.loads(cfg_path.read_text())
        cfg["protectedBranches"] = ["main", "master"]
        cfg_path.write_text(json.dumps(cfg))
        self.assertEqual(self.failing(self.rows()), [])

    def test_missing_master_answered_with_default_branch_is_skipped(self):
        self.redirect = True
        cfg_path = paths.crew_config_path("demo")
        cfg = json.loads(cfg_path.read_text())
        cfg["protectedBranches"] = ["main", "master"]
        cfg_path.write_text(json.dumps(cfg))
        self.assertEqual(self.failing(self.rows()), [])

    def test_invisible_repo_fails_instead_of_skipping_protection(self):
        self.invisible = True
        rows = self.rows()
        (row,) = [r for r in rows if not r[0]]
        self.assertIn("can't see owner/app with your gh login", row[2])
        self.assertFalse([r for r in rows if "branch protection" in r[1]])

    def test_other_branch_api_error_fails_the_row(self):
        self.api_error = True
        (row,) = [r for r in self.rows() if not r[0]]
        self.assertIn("branch protection main", row[1])
        self.assertIn("401", row[2])

    def test_main_doctor_exits_1_on_a_failing_row(self):
        from cli import main as cli_main
        d = self.deps()
        d["which"] = lambda n: None if n == "gh" else "/usr/bin/" + n
        real = doctor.check_all
        with mock.patch.object(doctor, "check_all", lambda crew, **kw: real(crew, d)), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_main.main(["doctor", "demo"]), 1)

    def test_missing_crew_dirs_fail(self):
        with state.unlocked(paths.crew_dir("demo")) as declared:
            declared["worktrees"] = None
            shutil.rmtree(paths.crew_dir("demo") / "worktrees")
        self.assertEqual(self.failing(self.rows()), ["demo: crew dirs"])

    def test_missing_sandbox_tool_fails_with_hint(self):
        self.missing.add("sandbox-exec" if doctor.sys.platform == "darwin" else "bwrap")
        (row,) = [r for r in self.rows() if not r[0]]
        self.assertIn("sandbox", row[1])

    def test_board_health_is_a_warning_during_init(self):
        d = self.deps()
        d["health"] = lambda url: False
        self.assertIn("board", " ".join(self.failing(doctor.check_all("demo", d))))
        self.assertEqual(self.failing(doctor.check_all("demo", d, during_init=True)), [])

    def test_broken_crew_config_is_one_row(self):
        paths.crew_config_path("demo").write_text("{nope")
        self.assertIn("demo: config", self.failing(self.rows()))


if __name__ == "__main__":
    unittest.main()
