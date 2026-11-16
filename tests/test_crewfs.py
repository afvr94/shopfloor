from __future__ import annotations

import ast
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from board import state as board_state
from engine.adapters.gitflow import base
from engine.lib import briefing, crewfs, paths, state, transport, worktrees
from tests.gitrepo import IDENT, commit_file, git, make_repo, tmpdir


class Unit(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        (self.root / "a" / "b").mkdir(parents=True)
        (self.root / "a" / "b" / "f.txt").write_text("hello")
        self.outside = self.base / "outside"
        (self.outside / "b").mkdir(parents=True)
        (self.outside / "b" / "f.txt").write_text("SECRET")

    def test_reads_a_plain_file(self):
        self.assertEqual(crewfs.read_bytes(self.root, "a/b/f.txt"), b"hello")

    def test_a_link_at_any_level_is_refused(self):
        cases = {"a": self.outside, "a/b": self.outside / "b", "a/b/f.txt": self.outside / "b" / "f.txt"}
        for rel, target in cases.items():
            with self.subTest(rel):
                self.setUp()
                p = self.root / rel
                if p.is_dir():
                    for x in sorted(p.rglob("*"), reverse=True):
                        x.rmdir() if x.is_dir() else x.unlink()
                    p.rmdir()
                else:
                    p.unlink()
                p.symlink_to(target)
                with self.assertRaises(crewfs.CrewFsError):
                    crewfs.read_bytes(self.root, "a/b/f.txt")

    def test_a_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.root / "a" / "pipe")
        t = time.monotonic()
        with self.assertRaises(crewfs.CrewFsError):
            crewfs.read_bytes(self.root, "a/pipe")
        self.assertLess(time.monotonic() - t, 2)

    def test_oversize_and_hard_links_are_refused(self):
        (self.root / "big").write_bytes(b"x" * 101)
        with self.assertRaises(crewfs.CrewFsError):
            crewfs.read_bytes(self.root, "big", limit=100)
        os.link(self.outside / "b" / "f.txt", self.root / "hard")
        with self.assertRaises(crewfs.CrewFsError):
            crewfs.read_bytes(self.root, "hard")

    def test_dot_dot_and_paths_outside_the_roots_are_refused(self):
        for rel in ("../outside/b/f.txt", "/etc/hosts", "a/../a/b/f.txt"):
            with self.subTest(rel), self.assertRaises(crewfs.CrewFsError):
                crewfs.read_bytes(self.root, rel)
        for p in (f"{self.root}/../outside/b/f.txt", str(self.outside / "b" / "f.txt"), "rel/path"):
            with self.subTest(p), self.assertRaises(crewfs.CrewFsError):
                crewfs.locate([self.root], p)
        self.assertEqual(crewfs.locate([self.root], f"{self.root}/a/b/f.txt"), (self.root, "a/b/f.txt"))

    def test_write_creates_exclusively_and_replaces_a_planted_link(self):
        target = self.outside / "b" / "f.txt"
        (self.root / "a" / "out").symlink_to(target)
        crewfs.write_bytes(self.root, "a/out", b"new")
        self.assertEqual(target.read_text(), "SECRET")
        self.assertFalse((self.root / "a" / "out").is_symlink())
        self.assertEqual((self.root / "a" / "out").read_text(), "new")
        self.assertEqual(sorted(p.name for p in (self.root / "a").iterdir()), ["b", "out"])
        with mock.patch.object(crewfs.secrets, "token_hex", return_value="fixed"):
            (self.root / "a" / ".out.fixed").symlink_to(target)
            with self.assertRaises(FileExistsError):
                crewfs.write_bytes(self.root, "a/out", b"again")
        self.assertEqual(target.read_text(), "SECRET")

    def test_move_in_renames_through_the_dir_fd(self):
        src = self.base / "src.txt"
        src.write_text("moved")
        (self.root / "a" / "dst").symlink_to(self.outside / "b" / "f.txt")
        crewfs.move_in(src, self.root, "a/dst")
        self.assertEqual(((self.root / "a" / "dst").read_text(), (self.outside / "b" / "f.txt").read_text()),
                         ("moved", "SECRET"))
        src.write_text("again")
        with self.assertRaises(FileExistsError):
            crewfs.move_in(src, self.root, "a/dst", replace=False)
        (self.root / "c").symlink_to(self.outside)
        with self.assertRaises(crewfs.CrewFsError):
            crewfs.move_in(src, self.root, "c/new")
        self.assertFalse((self.outside / "new").exists())

    def test_exists_counts_a_link_but_refuses_one_above(self):
        (self.root / "a" / "l").symlink_to(self.base / "nowhere")
        self.assertTrue(crewfs.exists(self.root, "a/l"))
        self.assertFalse(crewfs.exists(self.root, "a/none"))
        (self.root / "c").symlink_to(self.outside)
        with self.assertRaises(crewfs.CrewFsError):
            crewfs.exists(self.root, "c/b")


class Probes(unittest.TestCase):
    """The reviewer's probes, replayed against real repos: a crew can plant anything in its write roots."""

    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {**IDENT, "SHOPFLOOR_HOME": str(self.root / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.origin, self.main = make_repo(self.root)
        self.cfg = {"id": "crew", "repos": {"app": {"github": "o/app", "base": "main", "path": str(self.main)}},
                    "gitFlow": {"type": "pr-only", "testBranch": None, "mergeDenyPaths": []},
                    "protectedBranches": ["main"], "limits": {"bundleMaxMB": 1}}
        self.branch = "shopfloor/abc-1-x"
        state.save_ticket("crew", "ABC-1", branch=self.branch)
        self.wt = worktrees.prepare(self.cfg, "ABC-1", "app", self.branch)
        self.tk = paths.ticket_dir("crew", "ABC-1")
        self.secret = self.root / "outside" / "creds.json"
        self.secret.parent.mkdir()
        self.secret.write_text('{"auths": {"registry": {"auth": "SECacmeB64"}}, "title": "SECacmeB64"}')

    def push(self):
        return base.GitFlow(self.cfg).push("ABC-1", "app")

    def test_b_a_crew_edited_state_json_cannot_pick_the_branch(self):
        victim = "shopfloor/xyz-9-victim"
        other = self.root / "o2"
        git(self.root, "clone", "-q", str(self.origin), str(other))
        git(other, "checkout", "-q", "-b", victim)
        commit_file(other, "victim.txt", "v\n")
        git(other, "push", "-q", "origin", victim)
        before = git(self.origin, "rev-parse", victim)
        (self.tk / "state.json").write_text(json.dumps({"branch": victim}))
        transport.update(self.cfg, "app")
        git(self.wt, "branch", "-f", victim, "HEAD")
        git(self.wt, "bundle", "create", str(self.tk / "out-app.bundle"), victim)
        with self.assertRaises(base.GitFlowError):
            self.push()
        self.assertEqual(state.load_ticket("crew", "ABC-1")["branch"], self.branch)
        self.assertEqual(git(self.origin, "rev-parse", victim), before)

    def test_c_symlinked_state_and_briefing_in_the_ticket_folder_are_never_read(self):
        (self.tk / "state.json").symlink_to(self.secret)
        (self.tk / "briefing.json").symlink_to(self.secret)
        t = state.load_ticket("crew", "ABC-1")
        self.assertNotIn("auths", t)
        self.assertNotIn("SECRET", json.dumps(state.list_tickets("crew")))
        self.assertIsNone(board_state.briefing_data("crew", "ABC-1"))
        (self.tk / "briefing-draft.json").symlink_to(self.secret)
        from engine.lib import cli
        with mock.patch.object(cli.config, "load_crew", return_value={**self.cfg, "guard": {}}), \
                mock.patch.dict(os.environ, {"SHOPFLOOR_CREW": "crew"}), \
                mock.patch("sys.stdout") as out, mock.patch("sys.stderr"):
            code = cli.main(["briefing", "write", "ABC-1", str(self.tk / "briefing-draft.json")])
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", str(out.mock_calls))
        self.assertFalse((paths.ticket_state_dir("crew", "ABC-1") / "briefing.json").exists())
        good = {"title": "t", "whatWhy": "w", "risk": "r", "how": ["h"], "verify": ["v"], "files": ["f"], "prs": []}
        briefing.write_briefing("crew", "ABC-1", good)
        self.assertEqual(board_state.briefing_data("crew", "ABC-1"), good)

    def test_d_symlinked_worktrees_key_makes_prepare_refuse_and_write_nothing_outside(self):
        target = self.root / "outside" / "skills"
        target.mkdir()
        wts = paths.worktrees_dir("crew")
        (wts / "ABC-2").symlink_to(target)
        with self.assertRaises(crewfs.CrewFsError):
            worktrees.prepare(self.cfg, "ABC-2", "app", "shopfloor/abc-2")
        os.rename(wts / "ABC-1", wts / "ABC-1-old")
        (wts / "ABC-1").symlink_to(target)
        with self.assertRaises(crewfs.CrewFsError):
            worktrees.prepare(self.cfg, "ABC-1", "app", self.branch)
        self.assertEqual(list(target.iterdir()), [])

    def test_a_symlinked_ticket_folder_gets_no_bundle(self):
        target = self.root / "outside" / "tk"
        target.mkdir()
        os.rename(self.tk, self.root / "tk-old")
        self.tk.symlink_to(target)
        with self.assertRaises(crewfs.CrewFsError):
            worktrees.prepare(self.cfg, "ABC-1", "app", self.branch)
        self.assertEqual(list(target.iterdir()), [])

    def test_bad_bundles_are_refused_fast(self):
        out = self.tk / "out-app.bundle"
        plants = {
            "symlink": lambda: out.symlink_to(self.secret),
            "fifo": lambda: os.mkfifo(out),
            "hard link": lambda: os.link(self.secret, out),
            "oversize": lambda: out.write_bytes(b"# v2 git bundle\n".ljust(2 << 20, b"\0")),
        }
        for name, plant in plants.items():
            with self.subTest(name):
                if os.path.lexists(out):
                    out.unlink()
                plant()
                t = time.monotonic()
                with self.assertRaises(base.GitFlowError) as ctx:
                    self.push()
                self.assertLess(time.monotonic() - t, 10)
                self.assertNotIn("SECRET", str(ctx.exception))
        self.assertEqual(list(transport.root("crew").glob("tmp-*")), [])

    def test_bundle_checks_time_out_with_a_clear_message(self):
        commit_file(self.wt, "f.txt", "x\n")
        git(self.wt, "bundle", "create", str(self.tk / "out-app.bundle"), self.branch)
        with mock.patch.object(transport, "LOCAL_TIMEOUT", 0.001), self.assertRaises(base.GitFlowError) as ctx:
            self.push()
        self.assertIn("timed out after", str(ctx.exception))

    def test_a_bundle_within_the_limit_still_pushes(self):
        commit_file(self.wt, "f.txt", "x\n")
        git(self.wt, "bundle", "create", str(self.tk / "out-app.bundle"), self.branch)
        self.assertEqual(self.push(), self.branch)
        self.assertEqual(git(self.origin, "rev-parse", self.branch), git(self.wt, "rev-parse", "HEAD"))


ROOT = Path(__file__).resolve().parent.parent
CREW_PATHS = ("ticket_dir", "tickets_dir", "worktrees_dir", "crew_dirs", '"tickets"', '"worktrees"')
FILE_CALLS = {"open", "read_text", "read_bytes", "write_text", "write_bytes", "mkdir", "makedirs", "rename",
              "replace", "unlink", "touch", "symlink_to", "copy", "copy2", "copyfile", "copyfileobj"}
# These two create the write roots themselves, inside the crew state dir that only sf writes.
ALLOWED = {("cli/init.py", "ensure_dirs"), ("engine/lib/runner.py", "claude_cwd")}


def offenders(src: str) -> list[str]:
    """Functions that name a crew write root and also touch files directly instead of through crewfs."""
    tree, out = ast.parse(src), []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(w in ast.get_source_segment(src, fn) for w in CREW_PATHS):
            continue
        for c in ast.walk(fn):
            f = getattr(c, "func", None)
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            via_crewfs = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "crewfs"
            if isinstance(c, ast.Call) and name in FILE_CALLS and not via_crewfs:
                out.append(fn.name)
                break
    return out


class Static(unittest.TestCase):
    def test_the_scan_catches_a_direct_read(self):
        self.assertEqual(offenders("def f(c):\n    return (paths.ticket_dir(c, 'K') / 'x').read_text()\n"), ["f"])
        self.assertEqual(offenders("def f(c):\n    return crewfs.read_bytes(paths.tickets_dir(c), 'K/x')\n"), [])

    def test_only_crewfs_touches_files_under_the_crew_write_roots(self):
        bad = []
        for d in ("engine", "board", "cli"):
            for f in sorted((ROOT / d).rglob("*.py")):
                rel = f.relative_to(ROOT).as_posix()
                if f.name.startswith("test_") or rel == "engine/lib/crewfs.py":
                    continue
                bad += [f"{rel}:{fn}" for fn in offenders(f.read_text()) if (rel, fn) not in ALLOWED]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
