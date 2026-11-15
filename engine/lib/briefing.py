from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, paths, redact
from .state import _atomic_write

MAX_BYTES = 200_000
PR_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$")
_LISTS = ("how", "verify", "files")
_STRINGS = ("title", "whatWhy", "risk")
_KNOWN = set(_STRINGS) | set(_LISTS) | {"prs", "diagram", "size"}


class BriefingError(ValueError):
    pass


def validate_briefing(data: dict) -> list[str]:
    if not isinstance(data, dict):
        return ["briefing must be an object"]
    errs: list[str] = []

    def text(path: str, v: object) -> None:
        if not isinstance(v, str) or not v.strip():
            errs.append(f"{path}: must be a non-empty string")
        elif (kind := redact.find_sensitive(v)):
            errs.append(f"{path}: contains {kind}")

    for k in data:
        if k not in _KNOWN:
            errs.append(f"{k}: unknown field")
    for k in _STRINGS:
        text(k, data.get(k))
    for k in _LISTS:
        v = data.get(k)
        if not isinstance(v, list):
            errs.append(f"{k}: must be a list of strings")
            continue
        for i, item in enumerate(v):
            text(f"{k}[{i}]", item)
    prs = data.get("prs")
    if not isinstance(prs, list):
        errs.append("prs: must be a list")
        prs = []
    for i, p in enumerate(prs):
        if not isinstance(p, dict) or set(p) != {"repo", "number", "url"}:
            errs.append(f"prs[{i}]: must be an object with repo, number, url")
            continue
        text(f"prs[{i}].repo", p["repo"])
        if not isinstance(p["number"], int) or isinstance(p["number"], bool):
            errs.append(f"prs[{i}].number: must be an integer")
        if not isinstance(p["url"], str) or not PR_URL.match(p["url"]):
            errs.append(f"prs[{i}].url: must be a GitHub pull request URL")
    if "diagram" in data:
        text("diagram", data["diagram"])
    if "size" in data and data["size"] not in ("small", "full"):
        errs.append("size: must be 'small' or 'full'")
    try:
        if len(json.dumps(data).encode()) > MAX_BYTES:
            errs.append(f"briefing: larger than {MAX_BYTES} bytes")
    except (TypeError, ValueError):
        errs.append("briefing: not JSON-serializable")
    return errs


def write_briefing(crew_id: str, key: str, data: dict) -> Path:
    path = paths.ticket_dir(crew_id, key) / "briefing.json"
    if errs := validate_briefing(data):
        raise BriefingError("; ".join(errs))
    _atomic_write(path, json.dumps(data, indent=2) + "\n")
    return path


def briefing_url(crew_id: str, key: str) -> str:
    board = config.load_board()
    base = (board.get("linkBase") or f"http://{board['host']}:{board['port']}").rstrip("/")
    return f"{base}/briefing/{crew_id}/{key}"
