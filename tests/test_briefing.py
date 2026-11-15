from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from board import briefing_page
from engine.lib import briefing, paths

TOKEN = "gh" + "p_" + "a" * 20


def good():
    return {
        "title": "Fix the thing",
        "whatWhy": "It was broken.",
        "how": ["step one", "step two"],
        "risk": "Low.",
        "verify": ["run the tests"],
        "files": ["a.py"],
        "prs": [{"repo": "o/r", "number": 7, "url": "https://github.com/o/r/pull/7"}],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        old = os.environ.get("SHOPFLOOR_HOME")
        os.environ["SHOPFLOOR_HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.pop("SHOPFLOOR_HOME", None) if old is None
                        else os.environ.__setitem__("SHOPFLOOR_HOME", old))


class Writer(Base):
    def test_valid_briefing_written_to_ticket_dir(self):
        d = good()
        d["diagram"] = "flowchart TD\nA-->B"
        d["size"] = "small"
        p = briefing.write_briefing("crew-a", "ENG-1", d)
        self.assertEqual(p, paths.ticket_dir("crew-a", "ENG-1") / "briefing.json")
        self.assertEqual(json.loads(p.read_text()), d)
        self.assertEqual([x.name for x in p.parent.iterdir()], ["briefing.json"])

    def test_rejects_missing_fields_and_wrong_types(self):
        self.assertTrue(briefing.validate_briefing({}))
        d = good()
        d["how"] = "not a list"
        d["prs"][0]["number"] = "7"
        d["size"] = "huge"
        errs = " ".join(briefing.validate_briefing(d))
        for name in ("how", "prs[0].number", "size"):
            self.assertIn(name, errs)
        d = good()
        d["extra"] = "x"
        self.assertIn("extra", " ".join(briefing.validate_briefing(d)))
        with self.assertRaises(briefing.BriefingError):
            briefing.write_briefing("crew-a", "ENG-1", d)

    def test_rejects_sensitive_text(self):
        d = good()
        d["risk"] = "token " + TOKEN
        d["how"][1] = "mail a.b@example.com"
        errs = briefing.validate_briefing(d)
        self.assertTrue(any(e.startswith("risk") for e in errs), errs)
        self.assertTrue(any(e.startswith("how[1]") for e in errs), errs)
        d = good()
        d["prs"][0]["repo"] = TOKEN
        self.assertTrue(any(e.startswith("prs[0].repo") for e in briefing.validate_briefing(d)))

    def test_rejects_non_github_pr_url(self):
        d = good()
        d["prs"][0]["url"] = "https://evil.example/x"
        self.assertTrue(any("prs[0].url" in e for e in briefing.validate_briefing(d)))

    def test_rejects_over_200kb(self):
        d = good()
        d["how"] = ["x" * 1000] * 201
        self.assertTrue(any("200" in e for e in briefing.validate_briefing(d)))

    def test_rejects_newline_pr_url_and_over_caps(self):
        d = good()
        d["prs"][0]["url"] += "\n"
        self.assertTrue(any("prs[0].url" in e for e in briefing.validate_briefing(d)))
        for key, val in (("diagram", "x" * 6001), ("how", ["s"] * 13), ("files", ["f"] * 31),
                         ("title", "t" * 201), ("verify", ["v" * 401]), ("risk", "r" * 1501)):
            d = good()
            d[key] = val
            self.assertTrue(briefing.validate_briefing(d), key)
        d = good()
        d["prs"] = d["prs"] * 7
        self.assertTrue(briefing.validate_briefing(d))
        d = good()
        d["diagram"] = "x" * 6000
        self.assertEqual(briefing.validate_briefing(d), [])

    def test_url(self):
        self.assertEqual(briefing.briefing_url("c", "K-1"), "http://127.0.0.1:7420/briefing/c/K-1")
        (self.home / "board.json").write_text(json.dumps({"linkBase": "https://b.example/"}))
        self.assertEqual(briefing.briefing_url("c", "K-1"), "https://b.example/briefing/c/K-1")


class Renderer(unittest.TestCase):
    def test_render_escapes_everything(self):
        d = good()
        d["title"] = "<script>alert(1)</script>"
        d["how"] = ['<img src=x onerror="y">']
        d["prs"].append({"repo": "e/e", "number": 1, "url": "https://evil.example/x"})
        out = briefing_page.render_briefing(d, "c", "K-1")
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>alert", out)
        self.assertNotIn("<img", out)
        self.assertIn('href="https://github.com/o/r/pull/7"', out)
        self.assertIn('rel="noopener noreferrer"', out)
        self.assertNotIn('href="https://evil.example', out)
        self.assertTrue(out.startswith("<!doctype html>"))
        self.assertIn('<meta charset="utf-8">', out)

    def test_render_mermaid_strict(self):
        d = good()
        d["diagram"] = "flowchart TD\nA-->B<script>"
        out = briefing_page.render_briefing(d, "c", "K-1")
        self.assertIn('securityLevel: "strict"', out)
        self.assertIn("startOnLoad: true", out)
        self.assertIn("/static/mermaid.min.js", out)
        self.assertIn('<pre class="mermaid">flowchart TD\nA--&gt;B&lt;script&gt;</pre>', out)
        self.assertNotIn("mermaid.min.js", briefing_page.render_briefing(good(), "c", "K-1"))

    def test_brief_path(self):
        m = briefing_page.BRIEF_PATH.match("/briefing/crew-a/ENG-12")
        self.assertEqual(m.groups(), ("crew-a", "ENG-12"))
        self.assertIsNone(briefing_page.BRIEF_PATH.match("/briefing/../x"))
        self.assertIsNone(briefing_page.BRIEF_PATH.fullmatch("/briefing/a/B-1\n"))

    def test_newline_url_not_linked(self):
        d = good()
        d["prs"][0]["url"] += "\n"
        out = briefing_page.render_briefing(d, "c", "K-1")
        self.assertNotIn("<a ", out)


if __name__ == "__main__":
    unittest.main()
