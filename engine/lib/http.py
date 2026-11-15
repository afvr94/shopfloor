from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from engine.lib.redact import redact

_urlopen = urllib.request.urlopen


class HttpError(Exception):
    """`status` is the HTTP status, or None for transport failures (DNS, refused, timeout)."""

    def __init__(self, status: int | None, message: str):
        super().__init__(f"HTTP {status}: {redact(message)}" if status else redact(message))
        self.status = status


def _delay(retry_after, attempt: int) -> float:
    try:
        d = float(retry_after)
    except (TypeError, ValueError):
        d = float("nan")
    if not d >= 0:  # also rejects nan
        d = 2**attempt
    return min(d, 30)


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
        last = attempt == retries
        try:
            with _urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace")
                retry_after = e.headers.get("Retry-After") if e.headers else None
            finally:
                e.close()
            if (e.code == 429 or e.code >= 500) and not last:
                time.sleep(_delay(retry_after, attempt))
                continue
            raise HttpError(e.code, text) from None
        except OSError as e:  # URLError, socket timeouts, refused connections
            if not last:
                time.sleep(_delay(None, attempt))
                continue
            raise HttpError(None, f"{type(e).__name__}: {e}") from None
        try:
            return json.loads(body) if body else {}
        except ValueError:
            raise HttpError(200, "response is not valid JSON") from None
    raise AssertionError("unreachable")
