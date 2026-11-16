from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from board import listener
from board.briefing_page import BRIEF_PATH, render_briefing
from board.state import board_state, briefing_data, tick_running
from engine.lib import config, paths
from engine.lib.commands import CommandQueue

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 16 * 1024
MAX_TEXT = 4000
TYPES = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
         "woff2": "font/woff2", "svg": "image/svg+xml", "png": "image/png"}


def _spawn(argv: list[str]) -> None:
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / f"board-run-{argv[-1]}.log", "ab") as log:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


SPAWN = _spawn


def static_files() -> dict[str, Path]:
    if not STATIC_DIR.is_dir():
        return {}
    return {f.relative_to(STATIC_DIR).as_posix(): f for f in STATIC_DIR.rglob("*") if f.is_file()}


def make_handler(host: str, files: dict[str, Path]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Shopfloor"

        def _send(self, code: int, body: bytes | str, ctype: str = "text/plain; charset=utf-8") -> None:
            body = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: object) -> None:
            self._send(code, json.dumps(obj), "application/json")

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}", f"{host}:{port}"}

        def _crew(self, crew: object) -> str | None:
            ok = isinstance(crew, str) and paths.CREW_ID_RE.fullmatch(crew) and crew in config.list_crews()
            return crew if ok else None

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._send(403, "forbidden")
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._send(200, "ok")
            if path == "/api/state":
                return self._json(200, board_state())
            if path in ("/", "/index.html") or path.startswith("/static/"):
                name = "index.html" if path in ("/", "/index.html") else path[len("/static/"):]
                f = files.get(name)
                if f is None:
                    return self._send(404, "not found")
                return self._send(200, f.read_bytes(), TYPES.get(f.suffix.lstrip("."), "application/octet-stream"))
            m = BRIEF_PATH.fullmatch(path)
            if m and self._crew(m.group(1)):
                data = briefing_data(*m.groups())
                if data is not None:
                    return self._send(200, render_briefing(data, *m.groups()), TYPES["html"])
            self._send(404, "not found")

        def _body(self) -> dict | None:
            try:
                size = int(self.headers.get("Content-Length") or 0)
                if not 0 < size <= MAX_BODY:
                    return None
                body = json.loads(self.rfile.read(size))
            except (ValueError, TypeError):
                return None
            return body if isinstance(body, dict) else None

        def do_POST(self) -> None:
            if not self._host_ok() or self.headers.get("X-Shopfloor") != "1":
                return self._send(403, "forbidden")
            path = self.path.split("?", 1)[0]
            if path not in ("/api/run", "/api/command"):
                return self._send(404, "not found")
            req = self._body()
            if req is None:
                return self._json(400, {"ok": False, "message": "bad request"})
            crew = self._crew(req.get("crew"))
            if crew is None:
                return self._json(404, {"ok": False, "message": "unknown crew"})
            if path == "/api/run":
                if tick_running(crew):
                    return self._json(409, {"ok": False, "message": "a run is already active"})
                SPAWN([sys.executable, str(paths.REPO_ROOT / "bin" / "shopfloor"), "run", crew])
                return self._json(202, {"ok": True})
            key, text = req.get("key"), req.get("text")
            if not ((key is None or (isinstance(key, str) and paths.TICKET_KEY_RE.fullmatch(key)))
                    and isinstance(text, str) and text.strip() and len(text) <= MAX_TEXT):
                return self._json(400, {"ok": False, "message": "bad key or text"})
            ts = datetime.now(timezone.utc).isoformat()
            queued = CommandQueue(paths.crew_state_dir(crew)).append(key, "board", text, ts)
            self._json(200, {"ok": True, "queued": queued})

        def log_message(self, *args) -> None:
            pass

    return Handler


def make_server(board_cfg: dict) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((board_cfg["host"], int(board_cfg["port"])),
                               make_handler(board_cfg["host"], static_files()))


def serve(board_cfg: dict) -> None:
    server = make_server(board_cfg)
    stop = threading.Event()
    threading.Thread(target=listener.run_forever, args=(stop, board_cfg["pollSeconds"]), daemon=True).start()
    print(f"shopfloor board on http://{board_cfg['host']}:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
