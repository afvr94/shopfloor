from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, paths, redact
from .state import atomic_write

MAX_BYTES = 200_000
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")  # apply with .fullmatch
_LISTS = ("how", "verify", "files")
_STRINGS = ("title", "whatWhy", "risk")
# The renderer shows exactly these amounts; the validator rejects anything bigger.
MAX_LEN = {"title": 200, "whatWhy": 1500, "risk": 1500, "how": 400, "verify": 400, "files": 300, "diagram": 6000}
MAX_ITEMS = {"how": 12, "verify": 12, "files": 30, "prs": 6}
_KNOWN = set(_STRINGS) | set(_LISTS) | {"prs", "diagram", "size"}


class BriefingError(ValueError):
    pass


def validate_briefing(data: dict) -> list[str]:
    if not isinstance(data, dict):
        return ["briefing must be an object"]
    errs: list[str] = []

    def text(path: str, v: object, limit: int) -> None:
        if not isinstance(v, str) or not v.strip():
            errs.append(f"{path}: must be a non-empty string")
        elif len(v) > limit:
            errs.append(f"{path}: longer than {limit} characters")
        elif (kind := redact.find_sensitive(v)):
            errs.append(f"{path}: contains {kind}")

    for k in data:
        if k not in _KNOWN:
            errs.append(f"{k}: unknown field")
    for k in _STRINGS:
        text(k, data.get(k), MAX_LEN[k])
    for k in _LISTS:
        v = data.get(k)
        if not isinstance(v, list):
            errs.append(f"{k}: must be a list of strings")
            continue
        if len(v) > MAX_ITEMS[k]:
            errs.append(f"{k}: more than {MAX_ITEMS[k]} items")
        for i, item in enumerate(v):
            text(f"{k}[{i}]", item, MAX_LEN[k])
    prs = data.get("prs")
    if not isinstance(prs, list):
        errs.append("prs: must be a list")
        prs = []
    if len(prs) > MAX_ITEMS["prs"]:
        errs.append(f"prs: more than {MAX_ITEMS['prs']} items")
    for i, p in enumerate(prs):
        if not isinstance(p, dict) or set(p) != {"repo", "number", "url"}:
            errs.append(f"prs[{i}]: must be an object with repo, number, url")
            continue
        text(f"prs[{i}].repo", p["repo"], 200)
        if not isinstance(p["number"], int) or isinstance(p["number"], bool):
            errs.append(f"prs[{i}].number: must be an integer")
        if not isinstance(p["url"], str) or not PR_URL.fullmatch(p["url"]):
            errs.append(f"prs[{i}].url: must be a GitHub pull request URL")
    if "diagram" in data:
        text("diagram", data["diagram"], MAX_LEN["diagram"])
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
    atomic_write(path, json.dumps(data, indent=2) + "\n")
    return path


def briefing_url(crew_id: str, key: str) -> str:
    board = config.load_board()
    base = (board.get("linkBase") or f"http://{board['host']}:{board['port']}").rstrip("/")
    return f"{base}/briefing/{crew_id}/{key}"
