from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from engine.lib import http

TOKEN = "xox" + "b-" + "1" * 20


class FakeResp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body=b"", headers=None):
    return urllib.error.HTTPError("http://x", code, "err", headers or {}, io.BytesIO(body))


class Http(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(http.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def test_retries_429_with_retry_after(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(req)
            if len(calls) == 1:
                raise http_error(429, headers={"Retry-After": "0"})
            return FakeResp(b'{"ok": true}')

        with mock.patch.object(http, "_urlopen", fake):
            self.assertEqual(http.request_json("GET", "http://x"), {"ok": True})
        self.assertEqual(len(calls), 2)
        self.sleep.assert_called_once_with(0)

    def test_http_error_message_is_redacted(self):
        def fake(req, timeout=None):
            raise http_error(401, ("bad " + TOKEN).encode())

        with mock.patch.object(http, "_urlopen", fake):
            with self.assertRaises(http.HttpError) as cm:
                http.request_json("GET", "http://x")
        self.assertEqual(cm.exception.status, 401)
        self.assertIn("[redacted]", str(cm.exception))
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_json_body_and_form(self):
        seen = []

        def fake(req, timeout=None):
            seen.append(req)
            return FakeResp(b"{}")

        with mock.patch.object(http, "_urlopen", fake):
            http.request_json("POST", "http://x", json_body={"a": 1})
            http.request_json("POST", "http://x", form={"a": "b c"})
        self.assertEqual(seen[0].get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(seen[0].data), {"a": 1})
        self.assertEqual(seen[1].get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(seen[1].data, b"a=b+c")

    def test_5xx_exhausts_retries(self):
        n = []

        def fake(req, timeout=None):
            n.append(1)
            raise http_error(503)

        with mock.patch.object(http, "_urlopen", fake):
            with self.assertRaises(http.HttpError) as cm:
                http.request_json("GET", "http://x", retries=2)
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(len(n), 3)

    def test_4xx_not_retried(self):
        n = []

        def fake(req, timeout=None):
            n.append(1)
            raise http_error(404)

        with mock.patch.object(http, "_urlopen", fake):
            with self.assertRaises(http.HttpError):
                http.request_json("GET", "http://x")
        self.assertEqual(len(n), 1)


if __name__ == "__main__":
    unittest.main()
