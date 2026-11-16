from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from board import server, sessions
from engine.lib import paths, state
from engine.lib.commands import CommandQueue

CREW = {
    "id": "demo", "tracker": {"type": "linear", "tokenFile": "/secret/linear-token"},
    "chat": {"type": "slack", "tokenFile": "/secret/slack-token",
             "slack": {"channel": "C1", "ownerUserId": "U1"}},
    "repos": {"app": {"github": "o/app", "base": "main", "path": "/tmp/app"}},
}


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"SHOPFLOOR_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        (self.home / "crews").mkdir()
        self.crew("demo", CREW)
        self.start({"host": "127.0.0.1", "port": 0, "pollSeconds": 60, "crews": []})

    def crew(self, name, cfg):
        (self.home / "crews" / f"{name}.json").write_text(json.dumps(cfg))

    def start(self, cfg):
        self.srv = server.make_server(cfg)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def req(self, method, path, body=None, headers=None, host=None):
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read().decode()
        c.close()
        return r.status, data

    def post(self, path, body, **kw):
        return self.req("POST", path, body, {"X-Shopfloor": "1"}, **kw)


class ServerTest(BoardCase):
    def test_state_lists_crew_without_secrets(self):
        code, text = self.req("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(text)["crews"][0]["id"], "demo")
        self.assertNotIn("secret", text)
        self.assertNotIn("tokenFile", text)

    def test_polling_creates_nothing_on_disk(self):
        self.req("GET", "/api/state")
        self.req("GET", "/briefing/demo/ENG-1")
        self.req("GET", "/briefing/nope/ENG-1")
        self.assertFalse((self.home / "state").exists())

    def test_broken_crew_config_shows_error(self):
        self.crew("bad", {"id": "bad"})
        code, text = self.req("GET", "/api/state")
        crews = {c["id"]: c for c in json.loads(text)["crews"]}
        self.assertEqual(code, 200)
        self.assertIn("error", crews["bad"])
        self.assertIsNone(crews["demo"]["error"])

    def test_state_shows_tickets_and_questions(self):
        state.save_ticket("demo", "ENG-1", title="T", stage="needs_you", question="which one?")
        crew = json.loads(self.req("GET", "/api/state")[1])["crews"][0]
        self.assertEqual(crew["questions"][0]["question"], "which one?")
        self.assertEqual(crew["tickets"][0]["key"], "ENG-1")

    def test_briefing_route_renders_and_404s(self):
        d = paths.ticket_dir("demo", "ENG-1")
        d.mkdir(parents=True)
        (d / "briefing.json").write_text(json.dumps({"title": "Hello brief", "whatWhy": "because"}))
        code, text = self.req("GET", "/briefing/demo/ENG-1")
        self.assertEqual(code, 200)
        self.assertIn("Hello brief", text)
        for p in ("/briefing/demo/ENG-2", "/briefing/demo/../ENG-1", "/briefing/nope/ENG-1",
                  "/briefing/demo/ENG-1%0A"):
            self.assertEqual(self.req("GET", p)[0], 404, p)

    def test_static_whitelist(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "a.js").write_text("1")
            (Path(t) / "index.html").write_text("<p>hi</p>")
            (Path(t).parent / "secret.txt").write_text("x")
            with mock.patch.object(server, "STATIC_DIR", Path(t)):
                self.start({"host": "127.0.0.1", "port": 0, "pollSeconds": 60})
            self.assertEqual(self.req("GET", "/static/a.js")[0], 200)
            self.assertEqual(self.req("GET", "/")[1], "<p>hi</p>")
            for p in ("/static/b.js", "/static/../secret.txt", "/static/%2e%2e/secret.txt"):
                self.assertEqual(self.req("GET", p)[0], 404, p)
        self.assertEqual(self.req("GET", "/static/x")[0], 404)

    def test_missing_static_dir_is_fine(self):
        with mock.patch.object(server, "STATIC_DIR", self.home / "nope"):
            self.start({"host": "127.0.0.1", "port": 0, "pollSeconds": 60})
        self.assertEqual(self.req("GET", "/")[0], 404)
        self.assertEqual(self.req("GET", "/healthz")[0], 200)

    def test_post_requires_header_and_host(self):
        body = {"crew": "demo", "key": None, "text": "go"}
        self.assertEqual(self.req("POST", "/api/command", body)[0], 403)
        self.assertEqual(self.post("/api/command", body, host="evil.example")[0], 403)
        self.assertEqual(self.req("GET", "/healthz", host="evil.example")[0], 403)
        self.assertEqual(self.req("GET", "/healthz", host=f"localhost:{self.port}")[0], 200)

    def test_command_post_queues(self):
        self.assertEqual(self.post("/api/command", {"crew": "demo", "key": "ENG-1", "text": "approve"})[0], 200)
        row = CommandQueue(paths.crew_state_dir("demo")).pending()[0]
        self.assertEqual((row["source"], row["key"], row["cmd"]), ("board", "ENG-1", "approve"))

    def test_command_post_validates(self):
        bad = [{"crew": "nope", "key": None, "text": "x"}, {"crew": "demo", "key": "../x", "text": "x"},
               {"crew": "demo", "key": "ENG-1\n", "text": "x"}, {"crew": "demo", "key": None, "text": "  "},
               {"crew": "demo", "key": None, "text": "x" * 4001}, {"crew": "../demo", "key": None, "text": "x"}]
        for b in bad:
            self.assertIn(self.post("/api/command", b)[0], (400, 404), b)
        self.assertEqual(self.post("/api/command", {"crew": "demo", "key": None, "text": "x" * 4000})[0], 200)
        self.assertEqual(self.post("/api/command", {"crew": "demo", "text": "x", "pad": "y" * 17000})[0], 400)

    def test_run_spawns_and_is_refused_while_locked(self):
        with mock.patch.object(server, "SPAWN") as spawn:
            self.assertEqual(self.post("/api/run", {"crew": "demo"})[0], 202)
            self.assertEqual(spawn.call_args[0][0][1:], [str(paths.REPO_ROOT / "bin" / "shopfloor"), "run", "demo"])
            self.assertTrue(state.acquire_lock(paths.crew_state_dir("demo") / "tick.lock"))
            self.assertEqual(self.post("/api/run", {"crew": "demo"})[0], 409)
            self.assertEqual(spawn.call_count, 1)


class SessionsTest(unittest.TestCase):
    def test_sessions_only_crew_dirs(self):
        with tempfile.TemporaryDirectory() as t:
            home, projects = Path(t) / ".sf", Path(t) / "projects"
            (home / "state" / "demo").mkdir(parents=True)
            line = json.dumps({"type": "user", "timestamp": "2026-11-14T10:00:00Z",
                               "message": {"content": "/shopfloor:build ENG-1"}}) + "\n"
            for name in (str(home / "state" / "demo" / "worktrees"), str(Path(t) / "elsewhere")):
                d = projects / name.replace("/", "-").replace(".", "-")
                d.mkdir(parents=True)
                (d / "s1.jsonl").write_text(line)
            found = sessions.crew_sessions(projects, home)
            self.assertEqual(len(found), 1)
            self.assertEqual((found[0]["crew"], found[0]["kind"], found[0]["arg"]), ("demo", "build", "ENG-1"))


if __name__ == "__main__":
    unittest.main()
