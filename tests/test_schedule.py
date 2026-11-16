from __future__ import annotations

import unittest

from cli import schedule

S = {"start": "07:00", "until": "22:00", "everyHours": 3, "weekdaysOnly": False}


class Schedule(unittest.TestCase):
    def test_run_times_every_3h(self):
        self.assertEqual(
            schedule.run_times(S), [(7, 0), (10, 0), (13, 0), (16, 0), (19, 0), (22, 0)]
        )

    def test_launchd_weekdays(self):
        d = schedule.launchd_intervals({**S, "weekdaysOnly": True})
        self.assertEqual(len(d), 30)
        self.assertEqual({x["Weekday"] for x in d}, {1, 2, 3, 4, 5})
        self.assertEqual(len(schedule.launchd_intervals(S)), 6)

    def test_systemd_oncalendar(self):
        self.assertEqual(
            schedule.systemd_oncalendar({**S, "weekdaysOnly": True}),
            "Mon..Fri *-*-* 07,10,13,16,19,22:00:00",
        )
        self.assertEqual(schedule.systemd_oncalendar(S), "*-*-* 07,10,13,16,19,22:00:00")

    def test_rejects_bad_input(self):
        for bad in ({**S, "until": "07:00"}, {**S, "until": "06:00"},
                    {**S, "everyHours": 0}, {**S, "everyHours": 25}):
            with self.assertRaises(ValueError):
                schedule.run_times(bad)
