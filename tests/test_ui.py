from __future__ import annotations

import re
import unittest
from pathlib import Path

from board import server

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "board" / "static"


def _js() -> str:
    return (STATIC / "board.js").read_text(encoding="utf-8")


def _index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


class UiTests(unittest.TestCase):
    def test_index_has_no_inline_crew_ids(self):
        src = _index() + _js()
        self.assertNotRegex(src, r"\.id\s*[!=]==?\s*[\"']")
        self.assertNotRegex(src, r"[¿¡áéíóúñ]")

    def test_index_uses_header_on_posts(self):
        js = _js()
        fn = js[js.index("async function post("):]
        fn = fn[:fn.index("\n}\n") + 3]
        self.assertEqual(len(re.findall(r"method:\s*[\"']POST[\"']", js)), 1)
        self.assertIn('method: "POST"', fn)
        self.assertIn('"X-Shopfloor": "1"', fn)
        self.assertIn('"Content-Type": "application/json"', fn)

    def test_index_has_no_inline_script(self):
        self.assertNotRegex(_index(), r"<script(?![^>]*\bsrc=)")

    def test_sessions_rendered(self):
        self.assertIn("sessionPanel", _js())

    def test_no_inner_html_and_no_external_urls(self):
        src = _index() + _js()
        self.assertNotIn("innerHTML", src)
        self.assertNotIn("outerHTML", src)
        self.assertNotIn("insertAdjacentHTML", src)
        for tag in re.findall(r"<(?:script|link)\b[^>]*>", src):
            self.assertNotRegex(tag, r"(?:src|href)=[\"']?(?:https?:)?//", tag)
        self.assertNotRegex((STATIC / "board.css").read_text(), r"url\(\s*[\"']?(?:https?:)?//")

    def test_static_whitelist_matches_files(self):
        on_disk = {p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.is_file()}
        self.assertEqual(set(server.static_files()), on_disk)
        for need in ("index.html", "board.css", "board.js", "mermaid-init.js", "mermaid.min.js", "fonts/nunito.woff2",
                     "fonts/pixelify-sans.woff2", "fonts/OFL.txt", "icons/favicon.svg"):
            self.assertIn(need, on_disk)

    def test_mermaid_version_and_notice(self):
        self.assertIn(b"11.4.1", (STATIC / "mermaid.min.js").read_bytes())
        notice = (ROOT / "NOTICE").read_text()
        for word in ("Mermaid", "DOMPurify", "KaTeX", "Nunito", "Pixelify Sans"):
            self.assertIn(word, notice)
        self.assertNotIn("TODO", notice)
        self.assertIn("THIRD_PARTY_LICENSES", notice)

    def test_third_party_license_texts(self):
        text = (ROOT / "THIRD_PARTY_LICENSES").read_text()
        for need in ("Mermaid 11.4.1 - MIT License", "KaTeX", "DOMPurify 3.2.1", "Knut Sveidqvist", "Khan Academy",
                     "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION"):
            self.assertIn(need, text)
        self.assertEqual(text.count("Permission is hereby granted, free of charge"), 2)


if __name__ == "__main__":
    unittest.main()
