from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from engine.lib import lessons, paths

TEXT = "Run the linter before pushing so CI does not fail"


class LessonsTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        old = os.environ.get("SHOPFLOOR_HOME")
        os.environ["SHOPFLOOR_HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.pop("SHOPFLOOR_HOME", None) if old is None
                        else os.environ.__setitem__("SHOPFLOOR_HOME", old))

    def stored(self):
        return json.loads((paths.crew_state_dir("crew") / "lessons.json").read_text())

    def test_add_and_show_by_area(self):
        l = lessons.add("crew", "app/api", TEXT, "T-1: ci failed")
        self.assertEqual(l["id"], "L1")
        lessons.add("crew", "other", TEXT + " twice", "T-2")
        self.assertEqual([x["id"] for x in lessons.show("crew", area="app")], ["L1"])
        self.assertEqual(len(lessons.show("crew")), 2)
        self.assertEqual(lessons.show("nobody"), [])

    def test_duplicate_bumps_instead(self):
        a = lessons.add("crew", "app", TEXT, "one")
        b = lessons.add("crew", "app", TEXT.upper() + "!!", "two")
        self.assertEqual(b["id"], a["id"])
        self.assertEqual(b["count"], 2)
        self.assertEqual(len(lessons.show("crew")), 1)

    def test_refuses_personal_data_and_tokens(self):
        with self.assertRaisesRegex(lessons.LessonError, "an email address"):
            lessons.add("crew", "app", "Never email jane@example.com about build failures", "s")
        with self.assertRaisesRegex(lessons.LessonError, "an email address"):
            lessons.add("crew", "app", TEXT, "ticket from jane@example.com")
        with self.assertRaisesRegex(lessons.LessonError, "secret or token"):
            lessons.add("crew", "app", "Do not paste " + "ghp" + "_abcdef123456 into logs ever", "s")
        with self.assertRaises(lessons.LessonError):
            lessons.add("crew", "app", "too short", "s")
        with self.assertRaises(lessons.LessonError):
            lessons.add("crew", "Bad Area!", TEXT, "s")
        l = lessons.add("crew", "app", TEXT, "s")
        with self.assertRaisesRegex(lessons.LessonError, "an email address"):
            lessons.edit("crew", l["id"], "Ping jane@example.com when the build is red", "s")

    def test_bump_and_edit_refuse_bad_source(self):
        lessons.add("crew", "app", TEXT, "s")
        with self.assertRaisesRegex(lessons.LessonError, "an email address"):
            lessons.bump("crew", "L1", "ticket from jane@example.com")
        with self.assertRaisesRegex(lessons.LessonError, "an email address"):
            lessons.edit("crew", "L1", "Run the formatter before pushing a branch", "from jane@example.com")
        self.assertEqual(self.stored()["lessons"][0]["count"], 1)

    def test_malformed_entry_raises_lesson_error(self):
        for entry in ({"text": "x", "area": "a", "state": "active"}, {"id": 1, "text": "x", "state": "active"}, "junk", {"id": 1, "area": "a", "text": "x"}):
            (paths.crew_state_dir("crew") / "lessons.json").write_text(json.dumps({"next": 2, "lessons": [entry]}))
            with self.assertRaises(lessons.LessonError):
                lessons.show("crew")
            with self.assertRaises(lessons.LessonError):
                lessons.bump("crew", "L1", "s")

    def test_forget_hides_but_keeps(self):
        l = lessons.add("crew", "app", TEXT, "s")
        self.assertEqual(lessons.forget("crew", "l1")["state"], "forgotten")
        self.assertEqual(lessons.show("crew"), [])
        self.assertEqual(self.stored()["lessons"][0]["state"], "forgotten")
        with self.assertRaises(lessons.LessonError):
            lessons.forget("crew", l["id"])

    def test_bump_edit_and_id_forms(self):
        lessons.add("crew", "app", TEXT, "s")
        self.assertEqual(lessons.bump("crew", "1", "again")["count"], 2)
        e = lessons.edit("crew", "L1", "Run the formatter before pushing a branch", "reworded")
        self.assertEqual(e["text"], "Run the formatter before pushing a branch")
        with self.assertRaisesRegex(lessons.LessonError, "L99"):
            lessons.bump("crew", "L99", "s")

    def test_cap_retires_least_needed(self):
        for i in range(lessons.MAX_ACTIVE):
            lessons.add("crew", "app", f"Check item number {i} before shipping the change", "s")
        lessons.bump("crew", "L1", "s")
        lessons.add("crew", "app", "Check the newest item before shipping the change", "s")
        self.assertEqual(len(lessons.show("crew")), lessons.MAX_ACTIVE)
        states = {l["id"]: l["state"] for l in self.stored()["lessons"]}
        self.assertEqual(states[2], "retired")
        self.assertEqual(states[1], "active")

    def test_corrupt_store_raises(self):
        (paths.crew_state_dir("crew") / "lessons.json").write_text("{nope")
        with self.assertRaises(lessons.LessonError):
            lessons.show("crew")
        with self.assertRaises(lessons.LessonError):
            lessons.add("crew", "app", TEXT, "s")


if __name__ == "__main__":
    unittest.main()
