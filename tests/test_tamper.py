from __future__ import annotations

import contextlib
import io
import multiprocessing
import os
import signal
import stat
import threading
import unittest
from unittest import mock

from cli import doctor, main as cli_main
from engine.lib import lessons, paths, signing, state
from engine.lib.commands import CommandQueue, CommandQueueError
from tests.gitrepo import tmpdir
from tests.test_locking import foreign_write


def mode(p) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


def tampered_runs(crew: str) -> list[str]:
    return [r["note"] for r in state.recent_runs(crew, keep=1000) if r["result"] == "tampered"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"SHOPFLOOR_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.crew = paths.crew_state_dir("crew")
        self.ts = paths.ticket_state_dir("crew", "ENG-1")


class WindowReplay(Base):
    """A same-uid writer whose tmp+rename lands while sf has a folder open for its own write."""

    def test_a_cursor_replayed_during_a_log_run_window_is_flagged_and_refused(self):
        q = CommandQueue(self.crew)
        q.append(None, "board", "go", "t1")
        q.ack(1)
        q.append(None, "board", "cancel", "t2")
        old = q.cursor.read_text()
        q.ack(2)
        real = state.append_line

        def racing(path, line):
            with state.unlocked(path.parent):
                foreign_write(q.cursor, old)  # lands inside the runs.jsonl window
                real(path, line)
        with mock.patch.object(state, "append_line", racing):
            state.log_run("crew", None, "ok")
        with self.assertRaisesRegex(CommandQueueError, "changed by another writer"):
            q.pending()
        with self.assertRaisesRegex(CommandQueueError, "changed by another writer"):
            q.append(None, "board", "go", "t3")
        self.assertTrue(state.marker(q.cursor).exists())
        self.assertTrue(any("commands.cursor" in n for n in tampered_runs("crew")))
        self.assertEqual(mode(self.crew), 0o500)

    def test_a_ticket_replayed_during_a_save_window_stays_refused(self):
        state.save_ticket("crew", "ENG-1", stage="building")
        old = (self.ts / "state.json").read_text()
        state.save_ticket("crew", "ENG-1", stage="stopped")
        real = os.replace

        def racing(src, dst, *a, **kw):
            real(src, dst, *a, **kw)
            if str(dst).endswith("state.json"):
                foreign_write(dst, old)  # after sf's rename, before the folder relocks
        with mock.patch.object(state.os, "replace", racing):
            state.save_ticket("crew", "ENG-1", title="x")
        t = state.load_ticket("crew", "ENG-1")
        self.assertTrue(t["tampered"])
        self.assertIn("doctor --repair-tamper", t["question"])
        with self.assertRaises(signing.Tampered):
            state.save_ticket("crew", "ENG-1", stage="building")  # what `sf ticket set` calls: it can't clear it
        self.assertTrue(state.load_ticket("crew", "ENG-1")["tampered"])
        self.assertIn("repair-tamper crew", doctor._markers_problem("crew"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli_main.main(["doctor", "--repair-tamper", "crew"]), 0)
        self.assertIn("cleared", out.getvalue())
        self.assertFalse(state.marker(self.ts / "state.json").exists())
        self.assertIsNone(doctor._markers_problem("crew"))
        # the owner checked it: the replayed copy is still sf-signed, and reads again once relocked to 0400
        state.relock_all("crew")
        self.assertEqual(state.load_ticket("crew", "ENG-1")["stage"], "building")

    def test_a_planted_ticket_folder_is_flagged(self):
        state.save_ticket("crew", "ENG-1", stage="building")
        root = paths.ticket_state_root("crew")
        with state.unlocked(root):
            os.mkdir(root / "ENG-9")
        self.assertTrue(state.marker(root / "ENG-9").exists())
        self.assertTrue(state.load_ticket("crew", "ENG-9").get("tampered") is None)  # no state.json: blank
        with state.unlocked(root / "ENG-9") as declared:
            declared["state.json"] = None
            with open(root / "ENG-9" / "state.json", "w") as f:
                f.write("{}")
            os.chmod(root / "ENG-9" / "state.json", 0o400)
        self.assertTrue(state.load_ticket("crew", "ENG-9")["tampered"])

    def test_an_append_open_during_the_window_is_flagged(self):
        q = CommandQueue(self.crew)
        q.append(None, "board", "go", "t1")
        real = state.append_line

        def racing(path, line):
            real(path, line)
            with state.unlocked(path.parent):
                os.chmod(q.log, 0o600)  # append_line holds it 0600 for its own append; a racer gets in then
                with open(q.log, "a") as f:
                    f.write('{"x":1}\n')
                os.chmod(q.log, 0o400)
        with mock.patch.object(state, "append_line", racing):
            state.log_run("crew", None, "ok")
        with self.assertRaisesRegex(CommandQueueError, "changed by another writer"):
            q.pending()

    def test_legit_writes_from_many_threads_flag_nothing(self):
        q = CommandQueue(self.crew)

        def work(i):
            for j in range(5):
                state.save_ticket("crew", f"ENG-{i + 1}", title=f"t{j}")
                state.log_run("crew", f"ENG-{i + 1}", "ok")
                q.append(None, "board", "go", f"{i}.{j}")
            lessons.add("crew", "app", f"Run check number {i} before pushing to the branch", "T-1")
        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(tampered_runs("crew"), [])
        self.assertEqual(len(q.pending()), 30)
        self.assertEqual([t["key"] for t in state.list_tickets("crew") if t.get("tampered")], [])
        self.assertIsNone(doctor._markers_problem("crew"))


class Readers(Base):
    def test_a_record_that_is_not_0400_or_not_a_plain_file_is_refused(self):
        state.save_ticket("crew", "ENG-1", stage="building")
        p = self.ts / "state.json"
        os.chmod(p, 0o600)
        self.assertTrue(state.load_ticket("crew", "ENG-1")["tampered"])
        os.chmod(p, 0o400)
        self.assertFalse(state.load_ticket("crew", "ENG-1").get("tampered"))
        real = self.ts / "real.json"
        with state.unlocked(self.ts) as declared:
            declared.update({"real.json": None, "state.json": None})
            os.rename(p, real)
            os.symlink(real, p)
        self.assertTrue(state.load_ticket("crew", "ENG-1")["tampered"])
        with state.unlocked(self.ts) as declared:
            declared["state.json"] = None
            os.unlink(p)
            os.mkfifo(p, 0o400)
        self.assertTrue(state.load_ticket("crew", "ENG-1")["tampered"])  # refused, never blocks on the fifo


def _die_in(folder):
    with state.unlocked(folder):
        os.kill(os.getpid(), signal.SIGKILL)


class Relock(Base):
    def test_relock_all_closes_windows_a_sigkill_left_open(self):
        state.save_ticket("crew", "ENG-1", stage="building")
        state.log_run("crew", None, "ok")
        ctx = multiprocessing.get_context("fork")
        for folder in (self.crew, self.ts.parent, self.ts):
            p = ctx.Process(target=_die_in, args=(folder,))
            p.start()
            p.join()
            self.assertEqual(p.exitcode, -signal.SIGKILL)
        os.chmod(self.ts / "state.json", 0o600)  # a hand edit left open
        loose = state.open_locks("crew")
        self.assertEqual(len(loose), 4, loose)
        self.assertIn("open while idle", doctor._locks_problem("crew"))
        state.relock_all("crew")
        self.assertEqual(state.open_locks("crew"), [])
        self.assertIsNone(doctor._locks_problem("crew"))
        for d in (self.crew, self.ts.parent, self.ts):
            self.assertEqual(mode(d), 0o500, d)
        self.assertEqual(mode(self.ts / "state.json"), 0o400)

    def test_sf_startup_relocks(self):
        state.save_ticket("crew", "ENG-1", stage="building")
        os.chmod(self.crew, 0o700)
        from engine.lib import cli as sf
        with mock.patch.dict(os.environ, {"SHOPFLOOR_CREW": "crew"}), \
                mock.patch.object(sf.config, "load_crew", side_effect=sf.config.ConfigError("x")):
            sf.main(["config"])
        self.assertEqual(mode(self.crew), 0o500)


if __name__ == "__main__":
    unittest.main()
