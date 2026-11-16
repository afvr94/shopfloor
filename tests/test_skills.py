from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

from engine.lib.cli import _parser

SKILLS = Path(__file__).resolve().parent.parent / "engine" / "skills"
NAMES = ("rules", "tick", "spec", "build", "watch")
BANNED = ("pstack", "superpowers", "ponytail", "ecc:", "acme", "acme", "acme",
          "bash ", "python3 -c", "node -e")
DEAL_BREAKERS = (
    "done is ambiguous", "auth, permissions or sessions", "payments or billing",
    "secrets or credentials", "production data or a destructive migration",
    "deleting user data", "ci, deploy or infrastructure config", "grows well past the ticket",
)


def _texts() -> dict[str, str]:
    return {n: (SKILLS / n / "SKILL.md").read_text() for n in NAMES}


def _flags() -> dict[tuple[str, str], set[str]]:
    top = next(a for a in _parser()._actions if isinstance(a, argparse._SubParsersAction))
    out = {}
    for name, sp in top.choices.items():
        subs = next((a for a in sp._actions if isinstance(a, argparse._SubParsersAction)), None)
        for sub, p in (subs.choices.items() if subs else [("", sp)]):
            out[(name, sub)] = {o for a in p._actions for o in a.option_strings}
    return out


def _commands() -> dict[str, set[str]]:
    top = next(a for a in _parser()._actions if isinstance(a, argparse._SubParsersAction))
    out = {}
    for name, sp in top.choices.items():
        subs = next((a for a in sp._actions if isinstance(a, argparse._SubParsersAction)), None)
        out[name] = set(subs.choices) if subs else set()
    return out


class Skills(unittest.TestCase):
    def test_every_skill_has_name_and_description_frontmatter(self):
        for n, t in _texts().items():
            m = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", t)
            self.assertTrue(m, n)
            self.assertEqual(m.group(1), n)

    def test_every_sf_invocation_exists(self):
        cmds = _commands()
        seen = 0
        for n, t in _texts().items():
            for a, b in re.findall(r"`sf ([a-z-]+)(?: ([a-z-]+))?", t):
                seen += 1
                self.assertIn(a, cmds, f"{n}: sf {a}")
                if cmds[a] and b:
                    self.assertIn(b, cmds[a], f"{n}: sf {a} {b}")
        self.assertGreater(seen, 20)

    def test_every_sf_flag_exists(self):
        flags = _flags()
        for n, t in _texts().items():
            for span in re.findall(r"`(sf [^`]+)`", t):
                words = span.split()[1:]
                key = next((k for k in ((words[0], words[1] if len(words) > 1 else ""), (words[0], ""))
                            if k in flags), None)
                if key is None:
                    self.assertNotIn("--", span, f"{n}: {span}")
                    continue
                for f in re.findall(r"--[a-z-]+", span):
                    self.assertIn(f, flags[key], f"{n}: {span}")

    def test_no_third_party_plugins_or_blocked_commands(self):
        for n, t in _texts().items():
            for word in BANNED:
                self.assertNotIn(word, t.lower(), f"{n}: {word!r}")

    def test_rules_lists_core_deal_breakers(self):
        t = _texts()["rules"].lower().replace('"', "")
        for d in DEAL_BREAKERS:
            self.assertIn(d, t)


if __name__ == "__main__":
    unittest.main()
