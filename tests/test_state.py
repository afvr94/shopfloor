from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.lib import paths, state


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Base(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        old = os.environ.get("SHOPFLOOR_HOME")
        os.environ["SHOPFLOOR_HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.pop("SHOPFLOOR_HOME", None) if old is None
                        else os.environ.__setitem__("SHOPFLOOR_HOME", old))
        self.lock = self.home / "x.lock"

    def write_lock(self, pid, heartbeat=None):
        now = datetime.now(timezone.utc)
        self.lock.write_text(json.dumps({"pid": pid, "startedAt": iso(now), "heartbeat": iso(heartbeat or now)}))


class LockTest(Base):
    def test_lock_excludes_second_holder(self):
        self.assertTrue(state.acquire_lock(self.lock))
        self.assertFalse(state.acquire_lock(self.lock))
        self.assertEqual(state.read_lock(self.lock)["pid"], os.getpid())

    def test_lock_reclaims_dead_pid(self):
        self.write_lock(999999)
        self.assertIsNone(state.read_lock(self.lock))
        self.assertTrue(state.acquire_lock(self.lock))
        self.assertEqual(state.read_lock(self.lock)["pid"], os.getpid())

    def test_lock_reclaims_old_heartbeat(self):
        self.write_lock(os.getpid(), datetime.now(timezone.utc) - timedelta(minutes=46))
        self.assertIsNone(state.read_lock(self.lock))
        self.assertTrue(state.acquire_lock(self.lock))

    def test_lock_reclaims_unparseable(self):
        self.lock.write_text("{not json")
        self.assertTrue(state.acquire_lock(self.lock))

    def test_live_foreign_pid_blocks(self):
        self.write_lock(os.getppid())
        self.assertFalse(state.acquire_lock(self.lock))

    def test_permission_error_means_alive(self):
        self.write_lock(1)  # init: alive, not ours to signal
        self.assertFalse(state.acquire_lock(self.lock))

    def test_beat_and_release_only_for_owner(self):
        state.acquire_lock(self.lock)
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.write_lock(os.getpid(), old)
        state.beat(self.lock)
        self.assertGreater(state.read_lock(self.lock)["heartbeat"], iso(old))
        self.write_lock(os.getppid())
        state.beat(self.lock)
        state.release(self.lock)
        self.assertEqual(state.read_lock(self.lock)["pid"], os.getppid())
        self.write_lock(os.getpid())
        state.release(self.lock)
        self.assertFalse(self.lock.exists())
        state.release(self.lock)  # missing is fine
        state.beat(self.lock)
        self.assertFalse(self.lock.exists())


class RunsTest(Base):
    def test_runs_note_is_redacted_and_clipped(self):
        state.log_run("a", "ENG-1", "ok", "token " + "xox" + "b-1234567890-abcdefghijkl " + "y" * 900)
        run = state.recent_runs("a")[0]
        self.assertIn("[redacted]", run["note"])
        self.assertNotIn("xox" + "b-1234567890", run["note"])
        self.assertLessEqual(len(run["note"]), 500)
        self.assertEqual((run["key"], run["result"]), ("ENG-1", "ok"))
        self.assertIn("ts", run)

    def test_recent_runs_keeps_last_and_skips_torn_lines(self):
        for i in range(5):
            state.log_run("a", None, f"r{i}")
        with open(paths.crew_state_dir("a") / "runs.jsonl", "a") as f:
            f.write('{"ts": "torn\n')
        state.log_run("a", None, "r5")
        self.assertEqual([r["result"] for r in state.recent_runs("a", keep=3)], ["r3", "r4", "r5"])
        self.assertEqual(state.recent_runs("none"), [])


class TicketTest(Base):
    def test_load_missing_ticket_is_fresh_and_unwritten(self):
        t = state.load_ticket("a", "ENG-1")
        self.assertEqual((t["key"], t["stage"], t["prs"], t["history"]), ("ENG-1", None, [], []))
        self.assertFalse(paths.ticket_state_dir("a", "ENG-1").exists())

    def test_save_ticket_records_stage_history(self):
        state.save_ticket("a", "ENG-1", stage="claimed", title="T")
        t = state.save_ticket("a", "ENG-1", stage="planning")
        state.save_ticket("a", "ENG-1", stage="planning", branch="b")
        t = state.load_ticket("a", "ENG-1")
        self.assertEqual([h["stage"] for h in t["history"]], ["claimed", "planning"])
        self.assertTrue(all(h["ts"] for h in t["history"]))
        self.assertEqual((t["title"], t["branch"], t["updatedAt"] > ""), ("T", "b", True))
        with self.assertRaises(ValueError):
            state.save_ticket("a", "ENG-1", stage="bogus")
        with self.assertRaises(ValueError):
            state.save_ticket("a", "ENG-1", titel="typo")
        self.assertEqual(state.load_ticket("a", "ENG-1")["stage"], "planning")

    def test_stages(self):
        self.assertEqual(state.ACTIVE, state.STAGES[:6])
        self.assertEqual(state.ACTIVE[-1], "in_review")

    def test_list_tickets_sorted_by_updated(self):
        state.save_ticket("a", "ENG-1", stage="claimed")
        state.save_ticket("a", "ENG-2", stage="claimed")
        p = paths.ticket_state_dir("a", "ENG-1") / "state.json"
        d = json.loads(p.read_text())
        d["updatedAt"] = "2999-01-01T00:00:00Z"
        p.write_text(json.dumps(d))
        self.assertEqual([t["key"] for t in state.list_tickets("a")], ["ENG-1", "ENG-2"])
        self.assertEqual(state.list_tickets("nobody"), [])


if __name__ == "__main__":
    unittest.main()
