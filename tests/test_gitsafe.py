from __future__ import annotations

import ast
import os
import shutil
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine.adapters import gitflow
from engine.lib import gitsafe, paths, state, transport, worktrees
from tests.gitrepo import IDENT, commit_file, git, make_repo, tmpdir

ROOT = Path(__file__).resolve().parents[1]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {**IDENT, "SHOPFLOOR_HOME": str(self.root / "home")})
        p.start()
        self.addCleanup(p.stop)


class RefusalTest(Case):
    def test_refuses_crew_writable_targets_and_arguments_before_running_anything(self):
        wts, tickets = paths.worktrees_dir("crew"), paths.crew_state_dir("crew") / "tickets"
        (wts / "ABC-1" / "app").mkdir(parents=True)
        (tickets / "ABC-1").mkdir(parents=True)
        link = self.root / "link"
        link.symlink_to(wts / "ABC-1")
        upper = str(wts).replace("/worktrees", "/WORKTREES")
        with mock.patch.object(gitsafe, "run") as run:
            for where, args in [(wts / "ABC-1" / "app", ["status"]), (tickets / "ABC-1", ["status"]), (wts, []),
                                (link / "app", ["status"]), (upper + "/ABC-1", ["status"]),
                                (self.root, ["fetch", str(tickets / "ABC-1" / "out-app.bundle")]),
                                (self.root, ["clone", "x", str(wts / "ABC-1" / "y")])]:
                for bare in (False, True):
                    with self.subTest(str(where), bare=bare), self.assertRaises(gitsafe.CrewWritable):
                        gitsafe.git(where, *args, bare=bare)
            run.assert_not_called()
            gitsafe.git(paths.crew_state_dir("crew") / "transport", "status")
            run.assert_called()

    def test_global_credential_helper_is_kept(self):
        g = self.root / "global.cfg"
        g.write_text("[credential]\n\thelper = osxkeychain\n")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(g)}):
            flags = gitsafe.flags()
        self.assertEqual([f for f in flags if f.startswith("credential.")],
                         ["credential.helper=", "credential.helper=osxkeychain"])
        self.assertIn("core.hooksPath=/dev/null", flags)


def _sources():
    for d in ("engine", "cli", "board"):
        for p in (ROOT / d).rglob("*.py"):
            if not p.name.startswith("test_"):
                yield p


class StaticTest(unittest.TestCase):
    def test_only_gitsafe_starts_git(self):
        """No other module builds a git argv: every git sf runs passes gitsafe's crew-writable check."""
        for p in _sources():
            if p.name == "gitsafe.py":
                continue
            for node in ast.walk(ast.parse(p.read_text())):
                if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) \
                        and node.elts[0].value == "git":
                    self.fail(f"{p.relative_to(ROOT)}:{node.lineno} builds a git command outside gitsafe")

    def test_no_git_call_site_names_a_crew_folder(self):
        for p in _sources():
            for node in ast.walk(ast.parse(p.read_text())):
                if isinstance(node, ast.Call) and ast.unparse(node.func) in ("gitsafe.git", "_t", "g") \
                        and node.args:
                    src = ast.unparse(node.args[0])
                    self.assertFalse(any(w in src for w in ("worktrees", "ticket_dir", "tickets")),
                                     f"{p.relative_to(ROOT)}:{node.lineno} runs git in {src}")


PROBES = ("commondir", "parent repo", "config", "hooks", "attributes")


class ProbeTest(Case):
    """The reviewer's attacks on the old helper, planted in a crew clone, against the bundle flow."""

    def setUp(self):
        super().setUp()
        self.origin, self.main = make_repo(self.root, extra_branches=["staging"])
        self.cfg = {"id": "crew", "protectedBranches": ["main"],
                    "repos": {"app": {"github": "o/app", "base": "main", "path": str(self.main)}},
                    "gitFlow": {"type": "merge-to-test-branch", "testBranch": "staging", "mergeDenyPaths": []}}
        self.branch = "shopfloor/abc-1-x"
        state.save_ticket("crew", "ABC-1", branch=self.branch)
        self.wt = worktrees.prepare(self.cfg, "ABC-1", "app", self.branch)
        commit_file(self.wt, "f.txt", "x\n")
        self.tdir = paths.ticket_dir("crew", "ABC-1")
        git(self.wt, "bundle", "create", str(self.tdir / "out-app.bundle"), self.branch)
        self.marker = self.root / "ran"
        self.evil = self.root / "evil"
        self.evil.write_text(f"#!/bin/sh\ntouch {self.marker}\nexit 1\n")
        self.evil.chmod(0o755)

    def evil_config(self) -> str:
        s = self.evil
        return (f"[core]\n\tfsmonitor = {s}\n\tpager = {s}\n\tsshCommand = {s}\n\thooksPath = {self.root}\n"
                f"[remote \"origin\"]\n\turl = {self.origin}\n\tuploadpack = {s}\n\treceivepack = {s}\n"
                f"[filter \"x\"]\n\tclean = {s}\n\tsmudge = {s}\n[gpg]\n\tprogram = {s}\n"
                f"[credential]\n\thelper = !{s}\n")

    def plant(self, kind: str) -> None:
        g = self.wt / ".git"
        if kind == "commondir":
            other = self.root / "cd"
            shutil.copytree(g, other)
            (other / "config").write_text((other / "config").read_text() + self.evil_config())
            (g / "commondir").write_text(f"{other}\n")
        elif kind == "parent repo":
            (g / "HEAD").unlink()
            git(self.wt.parent, "init", "-q")
            (self.wt.parent / ".git" / "config").write_text(self.evil_config())
            (self.wt.parent / ".gitattributes").write_text("* filter=x\n")
        elif kind == "config":
            (g / "config").write_text((g / "config").read_text() + self.evil_config()
                                      + f"[include]\n\tpath = {self.root / 'inc'}\n")
            (self.root / "inc").write_text(self.evil_config())
        elif kind == "hooks":
            for h in ("pre-push", "post-checkout", "pre-commit", "post-merge", "reference-transaction"):
                (g / "hooks" / h).write_text(self.evil.read_text())
                (g / "hooks" / h).chmod(0o755)
        elif kind == "attributes":
            (g / "info").mkdir(exist_ok=True)
            (g / "info" / "attributes").write_text("* filter=x diff=x merge=x\n")
            (self.wt / ".gitattributes").write_text("* filter=x\n")

    def run_sf_flows(self):
        flow = gitflow.make_gitflow(self.cfg)
        worktrees.prepare(self.cfg, "ABC-1", "app", self.branch)
        flow.push("ABC-1", "app")
        transport.update(self.cfg, "app")
        transport.publish(transport.bundle_in(self.cfg, "app", self.branch, self.root / "in.bundle"), "crew",
                          "ABC-1", "in-app.bundle")
        oid = git(self.origin, "rev-parse", self.branch)
        flow.merge("ABC-1", "app", {"headRefName": self.branch, "headRefOid": oid, "isDraft": False,
                                     "statusCheckRollup": [{"conclusion": "SUCCESS"}], "files": []})

    def assert_sf_stays_out(self, run):
        for c in run.call_args_list:
            argv, cwd, env = c.args[0], c.kwargs.get("cwd"), c.kwargs.get("env") or {}
            for p in [cwd, env.get("GIT_DIR"), *[a for a in argv if str(a).startswith("/")]]:
                if p:
                    self.assertFalse(gitsafe.crew_writable(p), f"{argv} ran with {p}")

    def test_planted_code_never_runs(self):
        for kind in PROBES:
            with self.subTest(kind):
                self.setUp()
                self.plant(kind)
                with mock.patch.object(gitsafe, "run", wraps=gitsafe.run) as run:
                    self.run_sf_flows()
                self.assertFalse(self.marker.exists(), kind)
                self.assert_sf_stays_out(run)
                self.assertEqual(git(self.origin, "rev-list", "--count", "staging"), "3")
                if kind in ("commondir", "parent repo", "config"):
                    # control: plain git in the clone does run the plant, so the probe is live
                    subprocess.run(["git", "status"], cwd=self.wt, capture_output=True)
                    self.assertTrue(self.marker.exists(), kind)

    def test_config_race_is_moot(self):
        """Flip the clone's config between clean and evil while sf works: sf never reads it either way."""
        cfg = self.wt / ".git" / "config"
        good, bad = cfg.read_text(), cfg.read_text() + self.evil_config()
        stop = threading.Event()

        def flip():
            while not stop.is_set():
                for text in (bad, good):
                    tmp = cfg.with_name("config.tmp")
                    tmp.write_text(text)
                    os.replace(tmp, cfg)

        th = threading.Thread(target=flip)
        th.start()
        try:
            flow = gitflow.make_gitflow(self.cfg)
            for _ in range(5):
                worktrees.prepare(self.cfg, "ABC-1", "app", self.branch)
                flow.push("ABC-1", "app")
        finally:
            stop.set()
            th.join()
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
