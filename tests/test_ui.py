from __future__ import annotations

import re
import unittest
from pathlib import Path

from board import server

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "board" / "static"


def _index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


class UiTests(unittest.TestCase):
    def test_index_has_no_inline_crew_ids(self):
        src = _index()
        self.assertNotRegex(src, r"\.id\s*[!=]==?\s*[\"']")
        self.assertNotRegex(src, r"[¿¡áéíóúñ]")

    def test_index_uses_header_on_posts(self):
        src = _index()
        posts = [m.start() for m in re.finditer(r"method:\s*[\"']POST[\"']", src)]
        self.assertGreaterEqual(len(posts), 1)
        self.assertIn('"X-Shopfloor": "1"', src)
        self.assertIn('"Content-Type": "application/json"', src)
        self.assertEqual(len(re.findall(r"fetch\(", src)), 2)
        self.assertEqual(len(re.findall(r"function post\(", src)), 1)

    def test_no_inner_html_and_no_external_urls(self):
        src = _index()
        self.assertNotIn("innerHTML", src)
        self.assertNotIn("outerHTML", src)
        self.assertNotIn("insertAdjacentHTML", src)
        for tag in re.findall(r"<(?:script|link)\b[^>]*>", src):
            self.assertNotRegex(tag, r"(?:src|href)=[\"']?(?:https?:)?//", tag)
        self.assertNotRegex((STATIC / "board.css").read_text(), r"url\(\s*[\"']?(?:https?:)?//")

    def test_static_whitelist_matches_files(self):
        on_disk = {p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.is_file()}
        self.assertEqual(set(server.static_files()), on_disk)
        for need in ("index.html", "board.css", "mermaid.min.js", "fonts/nunito.woff2",
                     "fonts/pixelify-sans.woff2", "fonts/OFL.txt", "icons/favicon.svg"):
            self.assertIn(need, on_disk)

    def test_index_links_only_local_assets(self):
        src = _index()
        for ref in ("/static/board.css", "/static/icons/favicon.svg"):
            self.assertIn(ref, src)
            self.assertIn(ref.removeprefix("/static/"), {p.relative_to(STATIC).as_posix()
                                                         for p in STATIC.rglob("*") if p.is_file()})

    def test_mermaid_version_and_notice(self):
        head = (STATIC / "mermaid.min.js").read_text(encoding="utf-8", errors="replace")[:2000000]
        self.assertIn("11.4.1", head)
        notice = (ROOT / "NOTICE").read_text()
        for word in ("Mermaid", "DOMPurify", "KaTeX", "Nunito", "Pixelify Sans"):
            self.assertIn(word, notice)
        self.assertNotIn("TODO", notice)


if __name__ == "__main__":
    unittest.main()
