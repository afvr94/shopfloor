from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from engine.lib.redact import redact

_urlopen = urllib.request.urlopen


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {redact(message)}")
        self.status = status


def request_json(method, url, *, headers=None, json_body=None, form=None, timeout=30, retries=2) -> dict:
    headers = dict(headers or {})
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            retryable = e.code == 429 or e.code >= 500
            if retryable and attempt < retries:
                try:
                    delay = float(e.headers.get("Retry-After"))
                except (TypeError, ValueError):
                    delay = 2**attempt
                time.sleep(min(delay, 30))
                continue
            raise HttpError(e.code, e.read().decode("utf-8", "replace")) from None
    raise AssertionError("unreachable")
