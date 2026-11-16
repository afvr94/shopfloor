from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.adapters.chat.base import ThreadRef
from engine.adapters.tracker.base import Ticket
from engine.lib import cli, paths, state

TOKEN = "lin_" + "api_SECRETSECRET"


class FakeTracker:
    def __init__(self):
        self.calls = []

    def get(self, key):
        return Ticket(key, "id1", "Fix it", "body", ["shopfloor"], "Todo", f"http://t/{key}", None, [], "2026-01-01")

    def transition(self, key, to_key):
        self.calls.append(("transition", key, to_key))

    def comment(self, key, body):
        self.calls.append(("comment", key, body))
        return "c1"

    def update_comment(self, key, cid, body):
        self.calls.append(("update_comment", key, cid, body))

    def link_pr(self, key, url):
        self.calls.append(("link_pr", key, url))


class FakeChat:
    def __init__(self):
        self.calls = []

    def open_thread(self, ticket):
        self.calls.append(("open", ticket.key))
        return ThreadRef("C1", "1.5")

    def post(self, thread, text):
        self.calls.append(("post", thread, text))
        return "2.5"

    def ask(self, thread, question, options, rec):
        self.calls.append(("ask", question, options, rec))
        return "3.5"


class FakeFlow:
    def __init__(self, ok=True, why=""):
        self.ok, self.why, self.merged = ok, why, []

    def can_merge(self, repo, pr):
        return self.ok, self.why

    def merge(self, key, repo, pr):
        self.merged.append((key, repo))
        return "merged it"

    def ship(self, key, repo, title, body):
        self.body = body
        return "https://github.com/o/app/pull/7"

    def push(self, key, repo):
        self.pushed = (key, repo)
        return "shopfloor/eng-1-x"


class SfCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        env = mock.patch.dict(os.environ, {"SHOPFLOOR_HOME": str(self.home), "SHOPFLOOR_CREW": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.secret = self.home / "tok"
        self.secret.write_text(TOKEN)
        self.secret.chmod(0o600)
        self.write_cfg(dryRun=False)
        self.tracker, self.chat, self.flow = FakeTracker(), FakeChat(), FakeFlow()
        f = mock.patch.dict(cli.FACTORIES, {"tracker": lambda c: self.tracker, "chat": lambda c: self.chat,
                                            "gitflow": lambda c: self.flow})
        f.start()
        self.addCleanup(f.stop)

    def write_cfg(self, **over):
        cfg = {"id": "demo", "voice": {"name": "Demo", "style": "plain"},
               "tracker": {"type": "linear", "tokenFile": str(self.secret),
                           "linear": {"team": "ENG", "states": {"todo": "Todo", "inProgress": "In Progress"}},
                           "mayMove": ["todo->inProgress"]},
               "chat": {"type": "slack", "tokenFile": str(self.secret)},
               "repos": {"app": {"github": "o/app", "base": "main", "path": str(self.home / "app")}}, **over}
        p = paths.crew_config_path("demo")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg))

    def sf(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def runs(self):
        return state.recent_runs("demo")


class SfTest(SfCase):
    def test_dry_run_write_does_nothing_and_says_so(self):
        self.write_cfg(dryRun=True)
        code, out, _ = self.sf("tracker", "transition", "ENG-1", "inProgress")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("dry-run: would move ENG-1 todo->inProgress"), out)
        self.assertEqual(self.tracker.calls, [])
        self.assertEqual(self.runs()[-1]["result"], "dry-run")

    def test_dry_run_ship_and_merge(self):
        self.write_cfg(dryRun=True)
        body = self.home / "b.md"
        body.write_text("x")
        _, out, _ = self.sf("ship", "ENG-1", "app", "--title", "t", "--body-file", str(body))
        self.assertTrue(out.startswith("dry-run: would open a PR for ENG-1 in app"))
        _, out, _ = self.sf("merge", "ENG-1", "app", "12")
        self.assertTrue(out.startswith("dry-run: would merge app#12"))
        self.assertEqual(self.flow.merged, [])

    def test_comment_is_signed(self):
        f = self.home / "c.md"
        f.write_text("hello")
        code, out, _ = self.sf("tracker", "comment", "ENG-1", "--body-file", str(f))
        self.assertEqual((code, out.strip()), (0, "c1"))
        self.assertTrue(self.tracker.calls[0][2].endswith("— Demo, a Shopfloor crew"))
        self.sf("tracker", "comment", "ENG-1", "--body-file", str(f), "--update", "c9")
        self.assertEqual(self.tracker.calls[1][:3], ("update_comment", "ENG-1", "c9"))

    def test_chat_works_in_dry_run(self):
        self.write_cfg(dryRun=True)
        f = self.home / "t.md"
        f.write_text("hi")
        self.assertEqual(self.sf("chat", "open", "ENG-1")[0], 0)
        self.assertEqual(self.sf("chat", "post", "ENG-1", "--text-file", str(f))[0], 0)
        self.assertEqual([c[0] for c in self.chat.calls], ["open", "post"])

    def test_chat_open_stores_thread_on_ticket_and_is_idempotent(self):
        self.sf("chat", "open", "ENG-1")
        t = state.load_ticket("demo", "ENG-1")
        self.assertEqual(t["thread"], {"channel": "C1", "ts": "1.5"})
        self.assertEqual((t["title"], t["url"]), ("Fix it", "http://t/ENG-1"))
        self.sf("chat", "open", "ENG-1")
        self.assertEqual(len(self.chat.calls), 1)

    def test_chat_post_without_thread_fails(self):
        f = self.home / "t.md"
        f.write_text("hi")
        code, _, err = self.sf("chat", "post", "ENG-2", "--text-file", str(f))
        self.assertEqual(code, 1)
        self.assertIn("no thread for ENG-2; run sf chat open ENG-2", err)

    def test_chat_ask_sets_question_and_needs_you(self):
        self.sf("chat", "open", "ENG-1")
        code, _, _ = self.sf("chat", "ask", "ENG-1", "--question", "Which?", "--option", "A", "--option", "B",
                             "--recommend", "1")
        t = state.load_ticket("demo", "ENG-1")
        self.assertEqual((code, t["question"], t["stage"]), (0, "Which?", "needs_you"))

    def test_merge_refused_when_can_merge_false(self):
        self.flow = FakeFlow(False, "checks are red")
        with mock.patch.object(cli, "pr_info", return_value={}):
            code, _, err = self.sf("merge", "ENG-1", "app", "12")
        self.assertEqual(code, 1)
        self.assertIn("checks are red", err)
        self.assertEqual(self.flow.merged, [])

    def test_merge_success_and_paused(self):
        with mock.patch.object(cli, "pr_info", return_value={}):
            self.assertEqual(self.sf("merge", "ENG-1", "app", "12")[1].strip(), "merged it")
            self.write_cfg(paused=True)
            code, _, err = self.sf("merge", "ENG-1", "app", "12")
        self.assertEqual((code, err.strip()), (1, "paused"))

    def test_ship_records_pr(self):
        (paths.worktrees_dir("demo") / "ENG-1" / "app").mkdir(parents=True)
        body = self.home / "b.md"
        body.write_text("x")
        code, out, _ = self.sf("ship", "ENG-1", "app", "--title", "t", "--body-file", str(body))
        self.assertEqual((code, out.strip()), (0, "https://github.com/o/app/pull/7"))
        self.assertEqual(state.load_ticket("demo", "ENG-1")["prs"],
                         [{"repo": "app", "number": 7, "url": "https://github.com/o/app/pull/7"}])

    def test_ticket_set_parses_json_fields(self):
        code, _, _ = self.sf("ticket", "set", "ENG-1", "--stage", "building", "--field", "branch=b",
                             "--field", 'prs=[{"repo":"app"}]')
        t = state.load_ticket("demo", "ENG-1")
        self.assertEqual((code, t["stage"], t["branch"], t["prs"]), (0, "building", "b", [{"repo": "app"}]))
        self.assertEqual(self.sf("ticket", "set", "ENG-1", "--field", "bogus=1")[0], 1)

    def test_ticket_show_includes_absolute_dir(self):
        code, out, _ = self.sf("ticket", "show", "ENG-1")
        d = Path(json.loads(out)["dir"])
        self.assertEqual((code, d, d.is_dir()), (0, paths.ticket_dir("demo", "ENG-1"), True))
        self.assertTrue(d.is_absolute())

    def test_pause_and_resume_rewrite_config(self):
        self.sf("pause")
        self.assertTrue(json.loads(paths.crew_config_path("demo").read_text())["paused"])
        self.sf("resume")
        raw = json.loads(paths.crew_config_path("demo").read_text())
        self.assertFalse(raw["paused"])
        self.assertEqual(raw["id"], "demo")

    def test_build_calls_runner_with_crew_id(self):
        fake = mock.Mock()
        fake.status.return_value = {"pid": 1}
        import engine.lib
        with mock.patch.dict("sys.modules", {"engine.lib.runner": fake}), \
                mock.patch.object(engine.lib, "runner", fake, create=True):
            code, _, _ = self.sf("build", "start", "ENG-1", "ENG-2")
            self.assertEqual(code, 0)
            fake.start.assert_called_once_with("demo", ["ENG-1", "ENG-2"])
            code, out, _ = self.sf("build", "status")
            self.assertEqual(code, 0)
            fake.status.assert_called_once_with("demo")

    def test_errors_are_redacted(self):
        self.tracker.transition = mock.Mock(side_effect=cli.TrackerError(f"boom {TOKEN}"))
        code, _, err = self.sf("tracker", "transition", "ENG-1", "inProgress")
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET", err)

    def test_missing_crew_env_exits_2(self):
        del os.environ["SHOPFLOOR_CREW"]
        code, _, err = self.sf("config")
        self.assertEqual(code, 2)
        self.assertIn("SHOPFLOOR_CREW is not set", err)

    def test_config_prints_no_secret_paths_content(self):
        code, out, _ = self.sf("config")
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(str(self.secret), out)
        d = json.loads(out)
        self.assertEqual((d["id"], d["voice"]["name"], d["dryRun"], d["gitFlow"]["type"]), ("demo", "Demo", False, "pr-only"))

    def test_unknown_subcommand_exits_2(self):
        self.assertEqual(self.sf("tracker", "explode")[0], 2)
        self.assertEqual(self.sf("nope")[0], 2)

    def test_sf_redacts_outgoing_text(self):
        f = self.home / "c.md"
        f.write_text(f"see {TOKEN} and " + "gh" + "o_abcdef123456")
        self.sf("tracker", "comment", "ENG-1", "--body-file", str(f))
        self.assertNotIn("SECRET", self.tracker.calls[0][2])
        self.assertNotIn("abcdef123456", self.tracker.calls[0][2])
        self.sf("chat", "open", "ENG-1")
        self.sf("chat", "post", "ENG-1", "--text-file", str(f))
        self.sf("chat", "ask", "ENG-1", "--question", f"use {TOKEN}?", "--option", TOKEN, "--option", "B",
                "--recommend", "1")
        self.assertNotIn("SECRET", str(self.chat.calls))
        self.assertNotIn("SECRET", state.load_ticket("demo", "ENG-1")["question"])

    def test_candidates_skip_tickets_with_a_local_stage(self):
        self.tracker.list_candidates = lambda: [self.tracker.get("ENG-1"), self.tracker.get("ENG-2"),
                                                self.tracker.get("ENG-3")]
        state.save_ticket("demo", "ENG-1", stage="claimed")
        state.save_ticket("demo", "ENG-3", title="seen, never staged")
        code, out, _ = self.sf("tracker", "candidates")
        self.assertEqual((code, [t["key"] for t in json.loads(out)]), (0, ["ENG-2", "ENG-3"]))

    def test_get_reports_eligibility(self):
        self.assertTrue(json.loads(self.sf("tracker", "get", "ENG-1")[1])["eligible"])
        self.tracker.get = lambda key: Ticket(key, "i", "t", "b", ["Shopfloor", "HOLD"], "Todo", "u", None, [], "")
        self.assertFalse(json.loads(self.sf("tracker", "get", "ENG-1")[1])["eligible"])
        self.tracker.get = lambda key: Ticket(key, "i", "t", "b", [], "Todo", "u", None, [], "")
        self.assertFalse(json.loads(self.sf("tracker", "get", "ENG-1")[1])["eligible"])

    def test_config_includes_tracker_labels(self):
        d = json.loads(self.sf("config")[1])
        self.assertEqual(d["tracker"], {"label": "shopfloor", "holdLabels": ["hold"]})

    def test_ack_is_cumulative(self):
        from engine.lib.commands import CommandQueue
        q = CommandQueue(paths.crew_state_dir("demo"))
        for i in range(1, 4):
            q.append("ENG-1", "slack", "go", f"{i}.0")
        self.assertEqual(self.sf("commands", "ack", "2")[0], 0)
        self.assertEqual([r["seq"] for r in json.loads(self.sf("commands", "pending")[1])], [3])

    def test_runs_show_counts_since_latest_question(self):
        state.log_run("demo", "ENG-1", "build failed (exit 1)")
        state.log_run("demo", "ENG-2", "build timed out")
        with mock.patch.object(state, "_now", return_value="2999-01-01T00:00:00Z"):
            state.save_ticket("demo", "ENG-1", stage="needs_you")
            state.log_run("demo", "ENG-1", "build timed out")
        code, out, _ = self.sf("runs", "show", "ENG-1")
        self.assertEqual((code, [r["result"] for r in json.loads(out)]), (0, ["build timed out"]))

    def test_build_start_refuses_when_paused_busy_or_bad_key(self):
        from engine.lib import runner
        with mock.patch.object(runner, "start", side_effect=runner.RunnerBusy("a build run is already active")):
            code, _, err = self.sf("build", "start", "ENG-1")
        self.assertEqual((code, err.strip()), (1, "a build run is already active"))
        with mock.patch.object(runner, "start") as start:
            self.assertEqual(self.sf("build", "start", "ENG-1", "../x")[0], 2)
            self.write_cfg(paused=True)
            self.assertEqual(self.sf("build", "start", "ENG-1")[:3:2], (1, "paused\n"))
        start.assert_not_called()

    def test_write_commands_surface(self):
        self.assertEqual(cli.WRITE_COMMANDS, {("tracker", "transition"), ("tracker", "comment"),
                                              ("tracker", "link-pr"), ("ship", ""), ("merge", ""),
                                              ("push", ""), ("pr", "reply")})


class FakeGH:
    def __init__(self, answers):
        self.answers, self.calls, self.bodies = answers, [], []

    def __call__(self, args):
        self.calls.append(args)
        if "--body-file" in args:
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
        for prefix, ans in self.answers.items():
            if " ".join(args).startswith(prefix):
                if isinstance(ans, Exception):
                    raise ans
                return ans
        return ""


class SfPrTest(SfCase):
    def setUp(self):
        super().setUp()
        state.save_ticket("demo", "ENG-1", branch="shopfloor/eng-1-x",
                          prs=[{"repo": "app", "number": 7, "url": "https://github.com/o/app/pull/7"}])
        self.dir = paths.ticket_dir("demo", "ENG-1")

    def gh(self, answers):
        fake = FakeGH(answers)
        p = mock.patch.object(cli.flowbase, "GH", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def test_pr_view_reports_checks_and_new_comments_redacted(self):
        view = {"state": "OPEN", "mergeable": "MERGEABLE", "isDraft": False, "headRefOid": "abc",
                "reviewDecision": "CHANGES_REQUESTED",
                "statusCheckRollup": [{"name": "test", "conclusion": "FAILURE",
                                       "detailsUrl": "https://github.com/o/app/actions/runs/55/job/1"},
                                      {"name": "lint", "conclusion": "SUCCESS"}, {"name": "e2e", "status": "IN_PROGRESS"}],
                "comments": [{"author": {"login": "old"}, "body": "seen", "createdAt": "2026-01-01T00:00:00Z"},
                             {"author": {"login": "rev"}, "body": f"token {TOKEN}", "createdAt": "2026-01-03T00:00:00Z"}],
                "reviews": [{"author": {"login": "rev"}, "body": "", "state": "APPROVED", "submittedAt": "2026-01-04"}]}
        inline = [{"user": {"login": "rev"}, "body": "rename this", "path": "a.py", "line": 3,
                   "created_at": "2026-01-02T00:00:00Z"}]
        gh = self.gh({"pr view 7": json.dumps(view), "api repos/o/app/pulls/7/comments": json.dumps(inline)})
        state.save_ticket("demo", "ENG-1", reviewSeen="2026-01-01T00:00:00Z")
        code, out, _ = self.sf("pr", "view", "ENG-1")
        d = json.loads(out)
        (pr,) = d["prs"]
        self.assertEqual((code, pr["state"], pr["mergeable"], pr["headRefOid"]), (0, "OPEN", "MERGEABLE", "abc"))
        self.assertEqual(pr["checks"], {"green": False, "total": 3, "pending": 1, "failing": [
            {"name": "test", "url": "https://github.com/o/app/actions/runs/55/job/1"}]})
        self.assertEqual([(c["author"], c.get("path"), c.get("line")) for c in pr["newComments"]],
                         [("rev", "a.py", 3), ("rev", None, None)])
        self.assertNotIn("SECRET", out)
        self.assertEqual(d["latestCommentAt"], "2026-01-03T00:00:00Z")
        self.assertIn("-R", gh.calls[0])

    def test_pr_log_only_for_runs_on_the_ticket_branch(self):
        log = "\n".join(f"line {i}" for i in range(500)) + f"\n{TOKEN}"
        self.gh({"run view 55 -R o/app --json": json.dumps({"headBranch": "shopfloor/eng-1-x"}),
                 "run view 55 -R o/app --log-failed": log,
                 "run view 66 -R o/app --json": json.dumps({"headBranch": "main"})})
        code, out, _ = self.sf("pr", "log", "ENG-1", "55")
        lines = out.splitlines()
        self.assertEqual((code, len(lines), lines[-1]), (0, 400, "[redacted]"))
        code, _, err = self.sf("pr", "log", "ENG-1", "66")
        self.assertEqual(code, 1)
        self.assertIn("not a run of ENG-1's branch", err)

    def test_pr_reply_redacts_checks_the_pr_and_honours_dry_run(self):
        gh = self.gh({"pr comment": "https://github.com/o/app/pull/7#c1\n"})
        f = self.dir / "reply.md"
        f.write_text(f"fixed, {TOKEN}")
        code, out, _ = self.sf("pr", "reply", "ENG-1", "app", "7", "--body-file", str(f))
        self.assertEqual((code, out.strip()), (0, "https://github.com/o/app/pull/7#c1"))
        self.assertEqual(gh.bodies, ["fixed, [redacted]"])
        self.assertEqual(self.sf("pr", "reply", "ENG-1", "app", "8", "--body-file", str(f))[0], 1)
        self.write_cfg(dryRun=True)
        code, out, _ = self.sf("pr", "reply", "ENG-1", "app", "7", "--body-file", str(f))
        self.assertTrue(out.startswith("dry-run: would reply on app#7"), out)
        self.assertEqual(len(gh.bodies), 1)

    def test_push_dry_run_and_live(self):
        self.write_cfg(dryRun=True)
        code, out, _ = self.sf("push", "ENG-1", "app")
        self.assertTrue(out.startswith("dry-run: would push ENG-1's branch in app"), out)
        self.assertEqual(self.runs()[-1]["result"], "dry-run")
        self.write_cfg(dryRun=False)
        self.assertEqual(self.sf("push", "ENG-1", "app")[1].strip(), "pushed shopfloor/eng-1-x")
        self.assertEqual(self.flow.pushed, ("ENG-1", "app"))

    def test_ship_redacts_the_pr_body(self):
        f = self.dir / "pr-body.md"
        f.write_text(f"body {TOKEN}")
        self.sf("ship", "ENG-1", "app", "--title", "t", "--body-file", str(f))
        self.assertEqual(self.flow.body, "body [redacted]")

    def test_crew_files_must_be_real_files_in_the_write_roots(self):
        ok = self.dir / "c.md"
        ok.write_text("hi")
        link = self.dir / "link.md"
        link.symlink_to(paths.crew_config_path("demo"))
        hop = self.dir / "hop"
        hop.symlink_to(paths.crew_config_path("demo").parent)
        roots = mock.patch.object(cli, "_write_roots", return_value=[paths.worktrees_dir("demo"), self.dir.parent])
        roots.start()
        self.addCleanup(roots.stop)
        self.assertEqual(self.sf("tracker", "comment", "ENG-1", "--body-file", str(ok))[0], 0)
        for bad in (link, hop / "demo.json", paths.crew_config_path("demo"), self.secret):
            with self.subTest(str(bad)):
                code, _, err = self.sf("tracker", "comment", "ENG-1", "--body-file", str(bad))
                self.assertEqual(code, 1, err)
        self.assertEqual(len(self.tracker.calls), 1)


class SfGitTest(unittest.TestCase):
    def setUp(self):
        from tests.gitrepo import IDENT, make_repo, tmpdir
        self.tmp = tmpdir()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {**IDENT, "SHOPFLOOR_HOME": str(root / "home"), "SHOPFLOOR_CREW": "demo"})
        env.start()
        self.addCleanup(env.stop)
        self.origin, self.main = make_repo(root)
        secret = root / "tok"
        secret.write_text(TOKEN)
        secret.chmod(0o600)
        cfg = {"id": "demo", "dryRun": False, "tracker": {"type": "linear", "tokenFile": str(secret),
                                                          "linear": {"team": "ENG", "states": {"todo": "Todo"}}},
               "chat": {"type": "slack", "tokenFile": str(secret)},
               "repos": {"app": {"github": "o/app", "base": "main", "path": str(self.main)}}}
        p = paths.crew_config_path("demo")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg))
        self.wt = Path(SfCase.sf(self, "worktree", "prepare", "ENG-1", "app", "--branch", "shopfloor/eng-1-x")[1].strip())

    sf = SfCase.sf

    def test_push_takes_the_recorded_branch_from_the_out_bundle(self):
        from tests.gitrepo import commit_file, git
        commit_file(self.wt, "f.txt", "x\n")
        out = paths.ticket_dir("demo", "ENG-1") / "out-app.bundle"
        git(self.wt, "bundle", "create", str(out), "shopfloor/eng-1-x")
        self.assertEqual(self.sf("push", "ENG-1", "app")[:2], (0, "pushed shopfloor/eng-1-x\n"))
        self.assertEqual(git(self.origin, "rev-parse", "shopfloor/eng-1-x"), git(self.wt, "rev-parse", "HEAD"))
        state.save_ticket("demo", "ENG-1", branch="feature/x")
        code, _, err = self.sf("push", "ENG-1", "app")
        self.assertEqual(code, 1)
        self.assertIn("refusing to push feature/x", err)

    def test_sync_hands_origin_over_as_a_bundle(self):
        from tests.gitrepo import commit_file, git
        commit_file(self.wt, "mine.txt", "mine\n")
        commit_file(self.main, "theirs.txt", "theirs\n")
        git(self.main, "push", "-q", "origin", "main")
        code, out, _ = self.sf("sync", "ENG-1", "app")
        bundle = paths.ticket_dir("demo", "ENG-1") / "in-app.bundle"
        self.assertEqual(code, 0)
        self.assertIn(f"git fetch {bundle} '+refs/heads/*:refs/remotes/origin/*'", out)
        git(self.wt, "fetch", str(bundle), "+refs/heads/*:refs/remotes/origin/*")
        git(self.wt, "rebase", "origin/main")
        self.assertTrue((self.wt / "theirs.txt").exists())
        self.assertTrue((self.wt / "mine.txt").exists())

    def test_prepare_records_the_branch_and_keeps_an_existing_clone(self):
        from tests.gitrepo import git
        self.assertEqual(state.load_ticket("demo", "ENG-1")["branch"], "shopfloor/eng-1-x")
        git(self.wt, "config", "core.pager", "less")
        code, out, _ = self.sf("worktree", "prepare", "ENG-1", "app")
        self.assertEqual((code, out.strip()), (0, str(self.wt)))
