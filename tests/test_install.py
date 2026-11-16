from __future__ import annotations

import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import install

CREW = {"id": "demo", "schedule": {"start": "07:00", "until": "22:00", "everyHours": 3, "weekdaysOnly": True}}
BOARD = {"labelPrefix": "dev.shopfloor"}


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {"HOME": self.tmp, "SHOPFLOOR_HOME": self.tmp + "/sf",
                                           "PATH": "/x/bin:/usr/bin"})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []

    def runner(self, argv, **kw):
        self.calls.append(argv)
        return ""

    def at(self, platform):
        p = mock.patch.object(install.sys, "platform", platform)
        p.start()
        self.addCleanup(p.stop)

    def test_darwin_crew_plist(self):
        self.at("darwin")
        (path,) = install.install_crew(CREW, runner=self.runner)
        self.assertEqual(path, Path(self.tmp) / "Library/LaunchAgents/dev.shopfloor.crew.demo.plist")
        p = plistlib.loads(path.read_bytes())
        self.assertEqual(p["Label"], "dev.shopfloor.crew.demo")
        self.assertEqual(p["ProgramArguments"][-2:], ["run", "demo"])
        self.assertEqual(len(p["StartCalendarInterval"]), 30)
        self.assertEqual(p["EnvironmentVariables"]["PATH"], "/x/bin:/usr/bin")
        self.assertEqual(p["EnvironmentVariables"]["SHOPFLOOR_HOME"], self.tmp + "/sf")
        self.assertTrue(p["StandardOutPath"].endswith("logs/crew-demo.log"))
        uid = os.getuid()
        self.assertEqual(self.calls[-1], ["launchctl", "bootstrap", f"gui/{uid}", str(path)])
        self.assertEqual(self.calls[0][:2], ["launchctl", "bootout"])

    def test_darwin_board_and_prefix(self):
        self.at("darwin")
        (path,) = install.install_board({"labelPrefix": "x.y"}, runner=self.runner)
        p = plistlib.loads(path.read_bytes())
        self.assertEqual(p["Label"], "x.y.board")
        self.assertTrue(p["KeepAlive"] and p["RunAtLoad"])
        self.assertEqual(p["ProgramArguments"][-1], "board")

    def test_linux_crew_units(self):
        self.at("linux")
        paths = install.install_crew(CREW, runner=self.runner)
        d = Path(self.tmp) / ".config/systemd/user"
        self.assertEqual(paths, [d / "shopfloor-crew-demo.service", d / "shopfloor-crew-demo.timer"])
        timer = paths[1].read_text()
        self.assertIn("OnCalendar=Mon..Fri *-*-* 07,10,13,16,19,22:00:00", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("StandardOutput=append:", paths[0].read_text())
        self.assertEqual(self.calls, [["systemctl", "--user", "daemon-reload"],
                                      ["systemctl", "--user", "enable", "--now", "shopfloor-crew-demo.timer"]])

    def test_linux_board(self):
        self.at("linux")
        (_, *_), = [install.install_board(BOARD, runner=self.runner)]
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "enable", "--now", "shopfloor-board.service"])
        text = (Path(self.tmp) / ".config/systemd/user/shopfloor-board.service").read_text()
        self.assertIn("Restart=always", text)
        self.assertIn("WantedBy=default.target", text)

    def test_rejects_newline_and_bad_platform(self):
        self.at("linux")
        with mock.patch.dict(os.environ, {"PATH": "a\nExecStart=evil"}):
            with self.assertRaises(ValueError):
                install.install_board(BOARD, runner=self.runner)
        self.at("win32")
        with self.assertRaisesRegex(RuntimeError, "win32"):
            install.install_board(BOARD, runner=self.runner)

    def test_uninstall_touches_only_shopfloor_units(self):
        self.at("darwin")
        install.install_crew(CREW, runner=self.runner)
        la = Path(self.tmp) / "Library/LaunchAgents"
        other = la / "com.example.other.plist"
        other.write_text("x")
        sf = Path(self.tmp) / "sf"
        sf.mkdir(exist_ok=True)
        (sf / "keep.json").write_text("{}")
        self.calls.clear()
        removed = install.uninstall(runner=self.runner)
        self.assertEqual([p.name for p in removed], ["dev.shopfloor.crew.demo.plist"])
        self.assertTrue(other.exists() and (sf / "keep.json").exists())
        self.assertEqual(self.calls, [["launchctl", "bootout", f"gui/{os.getuid()}/dev.shopfloor.crew.demo"]])
        self.assertEqual(install.unit_paths(), [])

    def test_uninstall_linux(self):
        self.at("linux")
        install.install_crew(CREW, runner=self.runner)
        d = Path(self.tmp) / ".config/systemd/user"
        (d / "other.service").write_text("x")
        self.calls.clear()
        removed = install.uninstall(runner=self.runner)
        self.assertEqual(len(removed), 2)
        self.assertTrue((d / "other.service").exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", "shopfloor-crew-demo.timer"], self.calls)
