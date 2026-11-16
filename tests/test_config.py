from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from engine.lib import config, paths

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "crew.example.json"


def example():
    return json.loads(EXAMPLE.read_text())


class Base(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        old = os.environ.get("SHOPFLOOR_HOME")
        os.environ["SHOPFLOOR_HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.pop("SHOPFLOOR_HOME", None) if old is None
                        else os.environ.__setitem__("SHOPFLOOR_HOME", old))

    def errors(self, mutate):
        cfg = example()
        mutate(cfg)
        return "\n".join(config.validate(cfg))


class PathsTest(Base):
    def test_paths_follow_env_at_call_time(self):
        self.assertEqual(paths.shopfloor_home(), self.home)
        self.assertEqual(paths.crew_config_path("a"), self.home / "crews" / "a.json")
        d = paths.crew_state_dir("a")
        self.assertTrue(d.is_dir())
        self.assertEqual(paths.ticket_dir("a", "ENG-1").parent.parent, d)
        self.assertEqual(paths.worktrees_dir("a").parent, d)
        self.assertEqual(paths.logs_dir().parent, self.home)
        self.assertEqual(paths.SF_BIN, paths.ENGINE_DIR / "bin")
        self.assertEqual(paths.ENGINE_DIR, paths.REPO_ROOT / "engine")


class PathSafetyTest(Base):
    def test_unsafe_ids_and_keys_rejected(self):
        for bad in ("../x", "a/b", "", "abc\n", "A", "x" * 32):
            for fn in (paths.crew_config_path, paths.crew_state_dir, paths.worktrees_dir):
                with self.assertRaises(ValueError, msg=repr(bad)):
                    fn(bad)
            with self.assertRaises(ValueError):
                paths.ticket_dir(bad, "ENG-1")
        for bad in ("../x", "a/b", "", "ENG-1\n", "-x", "A" * 21):
            with self.assertRaises(ValueError, msg=repr(bad)):
                paths.ticket_dir("a", bad)
        self.assertFalse((self.home / "state").exists())

    def test_trailing_newline_rejected_by_validate(self):
        self.assertIn("id", self.errors(lambda c: c.__setitem__("id", "abc\n")))
        out = self.errors(lambda c: c["schedule"].__setitem__("start", "07:00\n"))
        self.assertIn("schedule.start", out)


class ConfigTest(Base):
    def test_example_config_loads_with_defaults(self):
        (self.home / "crews").mkdir()
        shutil.copy(EXAMPLE, self.home / "crews" / "demo.json")
        cfg = config.load_crew("demo")
        self.assertTrue(cfg["dryRun"] is True and cfg["gitFlow"]["type"] == "pr-only")
        self.assertEqual(cfg["limits"]["buildMaxMinutes"], 180)
        self.assertEqual(cfg["tracker"]["linear"]["label"], "shopfloor")
        self.assertEqual(cfg["repos"]["app"]["path"], os.path.expanduser("~/code/app"))
        self.assertEqual(config.list_crews(), ["demo"])

    def test_load_crew_defaults_fill_gaps(self):
        cfg = example()
        del cfg["limits"], cfg["gitFlow"], cfg["dryRun"]
        (self.home / "crews").mkdir()
        (self.home / "crews" / "demo.json").write_text(json.dumps(cfg))
        got = config.load_crew("demo")
        self.assertEqual(got["limits"], config.DEFAULTS["limits"])
        self.assertTrue(got["dryRun"])

    def test_protected_branches_default_to_main_and_master(self):
        cfg = example()
        cfg.pop("protectedBranches", None)
        (self.home / "crews").mkdir()
        (self.home / "crews" / "demo.json").write_text(json.dumps(cfg))
        self.assertEqual(config.load_crew("demo")["protectedBranches"], ["main", "master"])

    def test_secret_paths_add_user_entries_to_credential_defaults(self):
        cfg = example()
        cfg["guard"] = {"secretFiles": ["~/keys/deploy.pem"], "offLimits": ["/srv/private"]}
        cfg["tracker"]["tokenFile"] = "~/t/linear"
        (self.home / "crews").mkdir()
        (self.home / "crews" / "demo.json").write_text(json.dumps(cfg))
        got = config.secret_paths(config.load_crew("demo"))
        for p in ("~/.ssh", "~/.aws", "~/.config/gh", "~/.claude.json", "~/.claude/.credentials.json",
                  "~/keys/deploy.pem", "~/t/linear"):
            self.assertIn(os.path.expanduser(p), got)
        self.assertIn("/srv/private", got)
        self.assertIn(str(self.home / "secrets"), got)
        self.assertEqual(len(got), len(set(got)))

    def test_load_crew_lists_every_problem(self):
        cfg = example()
        cfg["id"] = "Bad Id"
        cfg["autoRecommend"] = "sometimes"
        (self.home / "crews").mkdir()
        (self.home / "crews" / "demo.json").write_text(json.dumps(cfg))
        with self.assertRaises(config.ConfigError) as cm:
            config.load_crew("demo")
        self.assertIn("id", str(cm.exception))
        self.assertIn("autoRecommend", str(cm.exception))

    def test_example_is_valid(self):
        self.assertEqual(config.validate(example()), [])

    def test_validate_rejects_bad_schedule(self):
        self.assertIn("schedule.until", self.errors(lambda c: c["schedule"].update(until="07:00")))
        self.assertIn("schedule.everyHours", self.errors(lambda c: c["schedule"].update(everyHours=0)))

    def test_validate_rejects_test_branch_protected(self):
        def m(c):
            c["gitFlow"].update(type="merge-to-test-branch", testBranch="main")
        self.assertIn("testBranch", self.errors(m))

    def test_validate_rejects_test_branch_equal_base(self):
        def m(c):
            c["protectedBranches"] = []
            c["gitFlow"].update(type="merge-to-test-branch", testBranch="main")
        self.assertIn("testBranch", self.errors(m))

    def test_validate_requires_test_branch_for_merge_to_test_branch(self):
        self.assertIn("testBranch", self.errors(lambda c: c["gitFlow"].update(type="merge-to-test-branch")))

    def test_validate_accepts_good_test_branch(self):
        self.assertEqual(self.errors(lambda c: c["gitFlow"].update(type="merge-to-test-branch", testBranch="staging")), "")

    def test_validate_rejects_unknown_maymove_state(self):
        self.assertIn("mayMove", self.errors(lambda c: c["tracker"]["mayMove"].append("todo->done")))

    def test_validate_rejects_bad_id_and_types(self):
        def m(c):
            c["id"] = "Bad Id"
            c["tracker"]["type"] = "jira"
            c["chat"]["type"] = "discord"
            c["autoRecommend"] = "sometimes"
        out = self.errors(m)
        for needle in ("id", "tracker.type", "chat.type", "autoRecommend"):
            self.assertIn(needle, out)

    def test_validate_repos_and_limits(self):
        def m(c):
            c["repos"]["app"]["github"] = "o/app\n"
            c["limits"]["maxParallel"] = 0
        out = self.errors(m)
        self.assertIn("repos.app.github", out)
        self.assertIn("limits.maxParallel", out)
        self.assertIn("repos", self.errors(lambda c: c.update(repos={})))

    def test_validate_does_not_mutate(self):
        cfg = example()
        before = copy.deepcopy(cfg)
        config.validate(cfg)
        self.assertEqual(cfg, before)


class SecretTest(Base):
    def write(self, content, mode):
        p = self.home / "secret"
        p.write_text(content)
        p.chmod(mode)
        return str(p)

    def test_read_secret_ok(self):
        self.assertEqual(config.read_secret(self.write("tok\n", 0o600)), "tok")

    def test_read_secret_refuses_world_readable(self):
        p = self.write("s3cr3t-value", 0o644)
        with self.assertRaises(config.ConfigError) as cm:
            config.read_secret(p)
        self.assertIn(p, str(cm.exception))
        self.assertIn("0600", str(cm.exception))

    def test_read_secret_refuses_missing_and_empty(self):
        with self.assertRaises(config.ConfigError):
            config.read_secret(str(self.home / "nope"))
        with self.assertRaises(config.ConfigError):
            config.read_secret(self.write("  \n", 0o600))

    def test_read_secret_never_puts_content_in_error(self):
        p = self.write("s3cr3t-value", 0o644)
        with self.assertRaises(config.ConfigError) as cm:
            config.read_secret(p)
        self.assertNotIn("s3cr3t-value", str(cm.exception))


class BoardTest(Base):
    def test_board_defaults(self):
        self.assertEqual(config.load_board(), {
            "host": "127.0.0.1", "port": 7420, "labelPrefix": "dev.shopfloor",
            "crews": [], "pollSeconds": 60, "linkBase": None})

    def test_list_crews_prefers_board_json(self):
        (self.home / "crews").mkdir()
        for n in ("b", "a"):
            (self.home / "crews" / f"{n}.json").write_text("{}")
        self.assertEqual(config.list_crews(), ["a", "b"])
        (self.home / "board.json").write_text(json.dumps({"crews": ["b"]}))
        self.assertEqual(config.list_crews(), ["b"])

    def test_voice_style_plain_maps_to_default_text(self):
        self.assertEqual(config.voice_style({"voice": {"style": "plain"}}),
                         "Plain, neutral and brief. No jokes, slang or emoji.")
        self.assertEqual(config.voice_style({"voice": {"style": "Pirate"}}), "Pirate")


if __name__ == "__main__":
    unittest.main()
