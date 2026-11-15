from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from . import paths


class ConfigError(ValueError):
    pass


DEFAULTS = {
    "dryRun": True,
    "protectedBranches": ["main", "master"],
    "paused": False,
    "autoRecommend": "questions",
    "limits": {
        "maxParallel": 2,
        "maxFixRounds": 3,
        "maxNewPerTick": 2,
        "buildMaxMinutes": 180,
        "tickMaxMinutes": 60,
    },
    "voice": {"name": "Shopfloor", "style": "plain"},
    "gitFlow": {"type": "pr-only", "testBranch": None, "mergeDenyPaths": []},
    "policyPacks": [],
    "guard": {"offLimits": [], "secretFiles": []},
    "tracker": {"linear": {"label": "shopfloor", "holdLabels": ["hold"]}},
}

BOARD_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 7420,
    "labelPrefix": "dev.shopfloor",
    "crews": [],
    "pollSeconds": 60,
    "linkBase": None,
}

PLAIN_STYLE = "Plain, neutral and brief. No jokes, slang or emoji."
ID_RE = paths.CREW_ID_RE
HHMM_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
GITHUB_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
MOVE_RE = re.compile(r"^(\w+)->(\w+)$")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _read_json(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text())
    except OSError as e:
        raise ConfigError(f"cannot read {what} {path}: {e.strerror}") from None
    except ValueError as e:
        raise ConfigError(f"{what} {path} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{what} {path} must be a JSON object")
    return data


def validate(cfg: dict) -> list[str]:
    c = _merge(DEFAULTS, cfg)
    errs: list[str] = []

    def d(key):
        v = c.get(key)
        return v if isinstance(v, dict) else {}

    if not (isinstance(c.get("id"), str) and ID_RE.fullmatch(c["id"])):
        errs.append("id must match ^[a-z0-9][a-z0-9-]{0,30}$")
    if d("tracker").get("type") != "linear":
        errs.append("tracker.type must be 'linear'")
    if d("chat").get("type") != "slack":
        errs.append("chat.type must be 'slack'")
    flow = d("gitFlow")
    if flow.get("type") not in ("pr-only", "squash-on-green", "merge-to-test-branch"):
        errs.append("gitFlow.type must be pr-only, squash-on-green or merge-to-test-branch")
    if c["autoRecommend"] not in ("never", "questions"):
        errs.append("autoRecommend must be 'never' or 'questions'")

    for k, v in d("limits").items():
        if not (isinstance(v, int) and not isinstance(v, bool) and v > 0):
            errs.append(f"limits.{k} must be a positive integer")

    sched = d("schedule")
    if sched:
        start, until = sched.get("start"), sched.get("until")
        for k, v in (("start", start), ("until", until)):
            if not (isinstance(v, str) and HHMM_RE.fullmatch(v)):
                errs.append(f"schedule.{k} must be HH:MM")
        if all(isinstance(v, str) and HHMM_RE.fullmatch(v) for v in (start, until)) and until <= start:
            errs.append("schedule.until must be later than schedule.start")
        every = sched.get("everyHours")
        if not (isinstance(every, int) and not isinstance(every, bool) and 1 <= every <= 24):
            errs.append("schedule.everyHours must be an integer from 1 to 24")

    repos = d("repos")
    if not repos:
        errs.append("repos must have at least one repo")
    for name, r in repos.items():
        r = r if isinstance(r, dict) else {}
        if not (isinstance(r.get("github"), str) and GITHUB_RE.match(r["github"])):
            errs.append(f"repos.{name}.github must look like owner/name")
        for k in ("base", "path"):
            if not (isinstance(r.get(k), str) and r[k]):
                errs.append(f"repos.{name}.{k} is required")

    states = d("tracker").get("linear", {}).get("states")
    states = states if isinstance(states, dict) else {}
    for move in d("tracker").get("mayMove", []):
        m = MOVE_RE.match(move) if isinstance(move, str) else None
        if not m or m.group(1) not in states or m.group(2) not in states:
            errs.append(f"tracker.mayMove entry {move!r} must be 'a->b' with both in tracker.linear.states")

    if flow.get("type") == "merge-to-test-branch":
        tb = flow.get("testBranch")
        bases = {r.get("base") for r in repos.values() if isinstance(r, dict)}
        if not (isinstance(tb, str) and tb):
            errs.append("gitFlow.testBranch is required for merge-to-test-branch")
        elif tb in c.get("protectedBranches", []):
            errs.append("gitFlow.testBranch must not be a protected branch")
        elif tb in bases:
            errs.append("gitFlow.testBranch must not be a repo base branch")
    return errs


def load_crew(crew_id: str) -> dict:
    cfg = _merge(DEFAULTS, _read_json(paths.crew_config_path(crew_id), f"crew '{crew_id}' config"))
    errs = validate(cfg)
    if errs:
        raise ConfigError(f"crew '{crew_id}' config is invalid:\n- " + "\n- ".join(errs))
    for r in cfg["repos"].values():
        r["path"] = os.path.expanduser(r["path"])
    for section in ("tracker", "chat"):
        if isinstance(cfg.get(section), dict) and "tokenFile" in cfg[section]:
            cfg[section]["tokenFile"] = os.path.expanduser(cfg[section]["tokenFile"])
    cfg["policyPacks"] = [os.path.expanduser(p) for p in cfg["policyPacks"]]
    return cfg


def read_secret(path: str) -> str:
    p = Path(os.path.expanduser(path))
    try:
        mode = p.stat().st_mode
    except OSError:
        raise ConfigError(f"secret file {p} is missing or unreadable") from None
    if mode & 0o077:
        raise ConfigError(f"secret file {p} must be mode 0600 (chmod 600 it)")
    try:
        value = p.read_text().strip()
    except OSError:
        raise ConfigError(f"secret file {p} is unreadable") from None
    if not value:
        raise ConfigError(f"secret file {p} is empty")
    return value


def load_board() -> dict:
    p = paths.shopfloor_home() / "board.json"
    return _merge(BOARD_DEFAULTS, _read_json(p, "board config")) if p.exists() else copy.deepcopy(BOARD_DEFAULTS)


def list_crews() -> list[str]:
    crews = load_board()["crews"]
    if crews:
        return list(crews)
    return sorted(p.stem for p in (paths.shopfloor_home() / "crews").glob("*.json"))


def voice_style(cfg: dict) -> str:
    style = (cfg.get("voice") or {}).get("style") or DEFAULTS["voice"]["style"]
    return PLAIN_STYLE if style == "plain" else style
