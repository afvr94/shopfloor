from __future__ import annotations

import unittest

from engine.lib import redact

TOKENS = [
    "xox" + "b-" + "1" * 20,
    "xox" + "p-" + "a1" * 10,
    "lin_" + "api_" + "a" * 20,
    "gh" + "p_" + "a" * 20,
    "github_" + "pat_" + "a" * 20,
    "sk-" + "ant-" + "abc-" + "d" * 20,
    "AK" + "IA" + "A" * 16,
    "ey" + "J" + "a" * 30,
]


class Redact(unittest.TestCase):
    def test_finds_tokens(self):
        for tok in TOKENS:
            self.assertEqual(redact.find_sensitive("see " + tok), redact.TOKEN_KIND, tok)

    def test_finds_personal(self):
        cases = {
            "mail me at a.b@example.com": "an email address",
            "call 555-123-4567": "a phone number",
            "ssn 123-45-6789": "an SSN-like number",
            "Date of Birth: x": "patient details",
        }
        for text, kind in cases.items():
            self.assertEqual(redact.find_sensitive(text), kind, text)

    def test_clean_text(self):
        self.assertIsNone(redact.find_sensitive("nothing to see here"))

    def test_token_wins_over_personal(self):
        text = "a@b.co " + TOKENS[0]
        self.assertEqual(redact.find_sensitive(text), redact.TOKEN_KIND)

    def test_redact_replaces_only_tokens(self):
        out = redact.redact("token " + TOKENS[0] + " ok a@b.co")
        self.assertEqual(out, "token [redacted] ok a@b.co")


if __name__ == "__main__":
    unittest.main()
