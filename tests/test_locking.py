from __future__ import annotations

import json
import os
import stat
import threading
import unittest
from unittest import mock

from engine.lib import lessons, paths, state
from engine.lib.commands import CommandQueue
from tests.gitrepo import tmpdir


def mode(p) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


def foreign_write(target, text: str = "forged") -> None:
    """What Claude Code's Write tool does, outside the sandbox: a temp file beside the target, then a rename."""
    tmp = f"{target}.tmp.{os.getpid()}.1"
    with open(tmp, "w") as f:
        f.write(text)
    os.rename(tmp, target)


class Locking(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"SHOPFLOOR_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        state.save_ticket("crew", "ENG-1", stage="building")
        state.log_run("crew", "ENG-1", "ok")
        q = CommandQueue(paths.crew_state_dir("crew"))
        q.append("ENG-1", "board", "approve", "1.0")
        q.ack(1)
        lessons.add("crew", "app", "Run the linter before pushing so CI does not fail", "T-1")
        state.acquire_lock(paths.tick_lock("crew"))
        self.crew = paths.crew_dir("crew")
        self.ts = paths.ticket_state_dir("crew", "ENG-1")

    def test_sfs_folders_and_files_are_locked_while_idle(self):
        for d in (self.crew, self.ts.parent, self.ts):
            self.assertEqual(mode(d), 0o500, d)
        for name in ("runs.jsonl", "commands.jsonl", "commands.cursor", "commands.lock", "lessons.json",
                     "lessons.json.lock", "lock", "lock.guard"):
            self.assertEqual(mode(self.crew / name), 0o400, name)
        for name in ("state.json", "state.lock"):
            self.assertEqual(mode(self.ts / name), 0o400, name)
        state.make_dir(paths.tickets_dir("crew"))
        state.make_dir(paths.worktrees_dir("crew"))
        for d in (paths.tickets_dir("crew"), paths.worktrees_dir("crew")):
            self.assertEqual(mode(d), 0o700, d)

    def test_a_foreign_write_and_rename_into_them_fails(self):
        for target in (self.ts / "state.json", self.ts / "briefing.json", self.crew / "commands.cursor",
                       self.crew / "commands.jsonl", self.crew / "lessons.json", self.ts.parent / "ENG-9",
                       self.crew / "runs.jsonl"):
            with self.subTest(target=target.name), self.assertRaises(PermissionError):
                foreign_write(target)
            with self.subTest(target=target.name, how="in place"), self.assertRaises(PermissionError):
                with open(target, "a") as f:
                    f.write("x")
        with self.assertRaises(PermissionError):
            os.mkdir(self.ts.parent / "ENG-9")

    def test_sfs_own_writes_still_work_and_relock(self):
        state.save_ticket("crew", "ENG-1", stage="in_review")
        state.save_ticket("crew", "ENG-2", stage="claimed")
        state.log_run("crew", None, "again")
        state.beat(paths.tick_lock("crew"))
        state.release(paths.tick_lock("crew"))
        self.assertEqual(state.load_ticket("crew", "ENG-1")["stage"], "in_review")
        self.assertFalse((self.crew / "lock").exists())
        self.assertEqual(state.recent_runs("crew")[-1]["result"], "again")
        for d in (self.crew, self.ts.parent, self.ts, paths.ticket_state_dir("crew", "ENG-2")):
            self.assertEqual(mode(d), 0o500, d)
        self.assertEqual(mode(self.ts / "state.json"), 0o400)

    def test_the_folder_is_relocked_when_the_write_raises(self):
        with mock.patch("os.replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            state.atomic_write(self.ts / "state.json", "{}")
        self.assertEqual(mode(self.ts), 0o500)
        self.assertEqual(sorted(os.listdir(self.ts)), ["state.json", "state.lock"])
        with self.assertRaises(RuntimeError), state.unlocked(self.crew):
            self.assertEqual(mode(self.crew), 0o700)
            raise RuntimeError
        self.assertEqual(mode(self.crew), 0o500)

    def test_concurrent_writers_keep_every_line(self):
        def work(i):
            for j in range(10):
                state.log_run("crew", None, f"w{i}-{j}")
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        runs = [r["result"] for r in state.recent_runs("crew", 1000) if r["result"].startswith("w")]
        self.assertEqual(len(runs), 80)
        self.assertEqual(mode(self.crew), 0o500)

    def test_nested_windows_on_one_folder_do_not_deadlock(self):
        with state.unlocked(self.crew), state.unlocked(self.crew):
            (self.crew / "x").write_text("y")
        self.assertEqual(mode(self.crew), 0o500)

    def test_only_sfs_own_kinds_of_folder_are_locked(self):
        home = paths.shopfloor_home()
        for p, want in ((home / "state" / "crew", True), (home / "state" / "crew" / "ticketstate", True),
                        (home / "state" / "crew" / "ticketstate" / "ENG-1", True),
                        (home / "state" / "crew" / "tickets", False), (home / "state" / "crew" / "worktrees", False),
                        (home / "state" / "crew" / "transport", False), (home / "state" / "crew" / "merge", False),
                        (home / "state", False), (home / "crews", False), (home / "logs", False)):
            self.assertEqual(state.is_locked(p), want, p)


if __name__ == "__main__":
    unittest.main()
