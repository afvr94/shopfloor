from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from engine.lib.config import BOARD_DEFAULTS, DEFAULTS

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
MANIFEST_SCOPES = {"chat:write", "chat:write.customize", "channels:history", "groups:history",
                   "channels:read", "groups:read"}


def key_paths(obj: dict, prefix: str = "") -> set:
    out = set()
    for k, v in obj.items():
        k = "<name>" if prefix == "repos" else k
        path = f"{prefix}.{k}" if prefix else k
        out.add(path)
        if isinstance(v, dict):
            out |= key_paths(v, path)
    return out


class Docs(unittest.TestCase):
    def test_config_doc_covers_every_key(self):
        text = (ROOT / "docs" / "config.md").read_text()
        example = json.loads((ROOT / "examples" / "crew.example.json").read_text())
        paths = key_paths(DEFAULTS) | key_paths(example) | {f"board.{k}" for k in BOARD_DEFAULTS}
        missing = sorted(p for p in paths if f"`{p}`" not in text)
        self.assertEqual(missing, [])

    def test_manifest_scopes(self):
        m = json.loads((ROOT / "examples" / "slack-app-manifest.json").read_text())
        self.assertEqual(set(m["oauth_config"]["scopes"]["bot"]), MANIFEST_SCOPES)
        self.assertNotIn("user", m["oauth_config"]["scopes"])
        settings = m.get("settings", {})
        self.assertFalse(settings.get("socket_mode_enabled", False))
        self.assertNotIn("event_subscriptions", settings)
        self.assertNotIn("interactivity", settings)
        self.assertNotIn("request_url", json.dumps(m))
        self.assertNotIn("redirect_urls", m["oauth_config"])

    def test_readme_links_resolve(self):
        for doc in DOCS:
            for target in re.findall(r"\]\(([^)\s]+)\)", doc.read_text()):
                if re.match(r"[a-z]+:|#", target):
                    continue
                self.assertTrue((doc.parent / target.split("#")[0]).exists(), f"{doc.name}: {target}")

    def test_safety_covers_required_topics(self):
        text = (ROOT / "docs" / "SAFETY.md").read_text().lower()
        for topic in ("pr-only", "squash-on-green", "merge-to-test-branch", "dry run", "guard",
                      "not a sandbox", "branch protection", "unattended", "127.0.0.1", "never expose",
                      "bubblewrap", "resume", "ticketstate", "o_nofollow", "bundlemaxmb",
                      "claude code's own hardening", "hmac-sha256", "record tampered", "--body-file -",
                      "detect a write, they don't prevent it", "secrets/signing/sf.key", "`0500`", "denieddomains",
                      "--repair-tamper", "relock sweep", "[::ffff:127.0.0.1]", "2.1.284", "board.allowremote",
                      "detected, not prevented",
                      "not that you chose what it says"):
            self.assertIn(topic, text)


if __name__ == "__main__":
    unittest.main()
