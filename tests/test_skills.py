from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

from engine.lib.cli import _parser

SKILLS = Path(__file__).resolve().parent.parent / "engine" / "skills"
NAMES = ("rules", "tick", "spec", "build", "review", "watch")
BANNED = ("pstack", "superpowers", "ponytail", "ecc:",
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

    def test_answers_reach_the_work_and_ready_tickets_are_swept(self):
        t = _texts()
        for n in ("rules", "tick", "spec", "build"):
            self.assertIn("<dir>/answers.md", t[n], n)
        self.assertIn("<dir>/question.md", t["tick"])
        self.assertIn("Ready sweep", t["tick"])
        self.assertIn("next tick's sweep", t["tick"])
        self.assertIn("/shopfloor:spec <KEY>", t["tick"])

    def test_every_queued_command_has_a_route_in_tick(self):
        tick = _texts()["tick"]
        for cmd in ("pause", "resume", "lesson", "forget", "answer", "approve", "adjust", "retry", "go", "cancel"):
            self.assertIn(f"`{cmd}", tick, cmd)
        for need in ("sf commands ack <seq>` once", "`eligible`", "sf runs show <KEY>", "empty `prs`"):
            self.assertIn(need, tick)

    def test_pause_and_resume_are_acknowledged_in_the_thread(self):
        # both are crew-wide and change nothing on the ticket, so the owner otherwise sees no reply
        lines = {l.split("`")[1]: l for l in _texts()["tick"].splitlines() if l.lstrip().startswith(("- `pause`", "- `resume`"))}
        for cmd in ("pause", "resume"):
            self.assertIn("sf chat post <KEY>", lines[cmd], cmd)

    def test_build_writes_a_briefing_draft_for_sf(self):
        build = _texts()["build"]
        self.assertIn("sf briefing write <KEY> <dir>/briefing-draft.json", build)
        self.assertNotIn("Write `<dir>/briefing.json`", build)

    def test_build_reviews_its_diff_before_shipping(self):
        t = _texts()
        build = t["build"]
        self.assertLess(build.index("/shopfloor:review <KEY>"), build.index("sf ship <KEY>"))
        self.assertIn("<dir>/review.md", build)
        self.assertIn("<dir>/review.md", t["review"])
        self.assertNotIn("sf ship", t["review"])

    def test_crews_reach_github_only_through_sf(self):
        t = _texts()
        for n in ("watch", "build", "review", "tick", "spec"):
            for word in ("`gh ", "git push", "git fetch origin", "git pull"):
                self.assertNotIn(word, t[n], f"{n}: {word}")
        for cmd in ("sf pr view <KEY>", "sf pr log <KEY>", "sf pr reply <KEY>", "sf push <KEY>", "sf sync <KEY>"):
            self.assertIn(cmd, t["watch"] + t["build"], cmd)

    def test_code_moves_as_bundles_in_the_ticket_folder(self):
        t = _texts()
        for n in ("build", "watch", "rules"):
            self.assertIn("git bundle create <dir>/out-<repo>.bundle <branch>", t[n], n)
            self.assertIn("git fetch <dir>/in-<repo>.bundle '+refs/heads/*:refs/remotes/origin/*'", t[n], n)
        self.assertNotIn("clone config changed", t["rules"] + t["tick"])

    def test_skills_never_pick_the_branch(self):
        t = _texts()
        for n, text in t.items():
            self.assertNotIn("--branch", text, n)
            self.assertIsNone(re.search(r"--field\s+branch=", text), n)
            self.assertIsNone(re.search(r"git (checkout|switch) -[bc]", text), n)
        self.assertIn("The ticket branch is `sf`'s", t["rules"])

    def test_rules_lists_core_deal_breakers(self):
        t = _texts()["rules"].lower().replace('"', "")
        for d in DEAL_BREAKERS:
            self.assertIn(d, t)


if __name__ == "__main__":
    unittest.main()
