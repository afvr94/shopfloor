from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
from pathlib import Path

from . import paths


class ConfigError(ValueError):
    pass


# credential files no crew command may read; a crew's guard.secretFiles adds to these
DEFAULT_SECRET_FILES = ["~/.ssh", "~/.aws", "~/.config/gh", "~/.netrc", "~/.npmrc", "~/.pypirc",
                        "~/.docker/config.json", "~/.kube", "~/.gnupg", "~/.claude/.credentials.json", "~/.claude.json"]

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
        "bundleMaxMB": 200,
    },
    "voice": {"name": "Shopfloor", "style": "plain"},
    "gitFlow": {"type": "pr-only", "testBranch": None, "mergeDenyPaths": []},
    "policyPacks": [],
    "guard": {"offLimits": [], "secretFiles": list(DEFAULT_SECRET_FILES)},
    "tracker": {
        "linear": {"label": "shopfloor", "holdLabels": ["hold"]},
        "github": {"label": "shopfloor", "holdLabels": ["hold"], "assignee": None,
                   "states": {"inProgress": "shopfloor:in-progress", "review": "shopfloor:review"}},
    },
}

BOARD_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 7420,
    "labelPrefix": "dev.shopfloor",
    "crews": [],
    "pollSeconds": 60,
    "linkBase": None,
    "allowRemote": False,
}

PLAIN_STYLE = "Plain, neutral and brief. No jokes, slang or emoji."
ID_RE = paths.CREW_ID_RE
HHMM_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
STAGES = ("todo", "inProgress", "review")
GITHUB_RE = re.compile(r"[\w.-]+/[\w.-]+")
TEAM_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,9}")
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
    ttype = d("tracker").get("type")
    if ttype not in ("linear", "github"):
        errs.append("tracker.type must be 'linear' or 'github'")
    block = d("tracker").get(ttype)
    block = block if isinstance(block, dict) else {}
    if ttype == "linear":
        team = block.get("team")
        if team is not None and not (isinstance(team, str) and TEAM_RE.fullmatch(team)):  # never echo it: a pasted token lands here too
            errs.append("tracker.linear.team must be a Linear team key, the prefix of its issue ids (like ENG)")
        if not d("tracker").get("tokenFile"):
            errs.append("tracker.tokenFile is required for linear")
    elif ttype == "github":
        if not (isinstance(block.get("repo"), str) and GITHUB_RE.fullmatch(block["repo"])):
            errs.append("tracker.github.repo must look like owner/name")
        if "tokenFile" in d("tracker"):
            errs.append("tracker.tokenFile is not used with github; the tracker uses your gh login")
        states = block.get("states") if isinstance(block.get("states"), dict) else {}
        for k in sorted(set(states) - {"inProgress", "review"}):
            errs.append(f"tracker.github.states may only name inProgress and review; {k} has no label")
        names = list(states.values())
        if not all(isinstance(n, str) and n for n in names):
            errs.append("tracker.github.states labels must be non-empty strings")
        else:
            lower = [n.lower() for n in names]
            if len(set(lower)) < len(lower):
                errs.append("tracker.github.states labels must differ from each other")
            # a state label equal to the opt-in or a hold label hides every issue, and transition would strip it
            others = [block.get("label"), *(block.get("holdLabels") or [])]
            if set(lower) & {o.lower() for o in others if isinstance(o, str)}:
                errs.append("tracker.github.states labels must differ from the opt-in label and hold labels")
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
        if not (isinstance(r.get("github"), str) and GITHUB_RE.fullmatch(r["github"])):
            errs.append(f"repos.{name}.github must look like owner/name")
        for k in ("base", "path"):
            if not (isinstance(r.get(k), str) and r[k]):
                errs.append(f"repos.{name}.{k} is required")

    if ttype == "linear":
        states = block.get("states")
        keys = states if isinstance(states, dict) else {}
        where = "tracker.linear.states"
    else:
        keys = STAGES
        where = ", ".join(STAGES)
    for move in d("tracker").get("mayMove", []) if ttype in ("linear", "github") else []:
        m = MOVE_RE.match(move) if isinstance(move, str) else None
        if not m or m.group(1) not in keys or m.group(2) not in keys:
            errs.append(f"tracker.mayMove entry {move!r} must be 'a->b' with both in {where}")

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


def secret_paths(cfg: dict) -> list[str]:
    """Every path crews may not read: credential defaults, guard.secretFiles and offLimits, the secrets dir
    and the crew's token files."""
    g = cfg.get("guard") or {}
    tokens = [(cfg.get(s) or {}).get("tokenFile") for s in ("tracker", "chat")]
    raw = [*DEFAULT_SECRET_FILES, *g.get("secretFiles", []), *g.get("offLimits", []),
           str(paths.shopfloor_home() / "secrets"), *tokens]
    return list(dict.fromkeys(os.path.expanduser(str(p)) for p in raw if p))


def read_secret(path: str) -> str:
    p = Path(os.path.expanduser(path))
    try:
        mode = p.stat().st_mode
    except OSError:
        raise ConfigError(f"secret file {p} is missing or unreadable") from None
    if mode & 0o077:
        raise ConfigError(f"secret file {p} must be mode 0400 or 0600 (chmod 400 it)")
    try:
        value = p.read_text().strip()
    except OSError:
        raise ConfigError(f"secret file {p} is unreadable") from None
    if not value:
        raise ConfigError(f"secret file {p} is empty")
    return value


def is_loopback(host: object) -> bool:
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(str(host).strip("[]"))
    except ValueError:
        return False
    return ip.is_loopback or bool(getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback)


def load_board() -> dict:
    p = paths.shopfloor_home() / "board.json"
    board = _merge(BOARD_DEFAULTS, _read_json(p, "board config")) if p.exists() else copy.deepcopy(BOARD_DEFAULTS)
    # The board has no login: anyone who reaches it can queue owner commands.
    if not is_loopback(board.get("host")) and board.get("allowRemote") is not True:
        raise ConfigError(f"board.host {board.get('host')!r} is not a loopback address. The board has no login; "
                          "keep it on 127.0.0.1, or set board.allowRemote to true if you put your own access "
                          "control in front of it")
    return board


def list_crews() -> list[str]:
    crews = load_board()["crews"]
    if crews:
        return list(crews)
    return sorted(p.stem for p in (paths.shopfloor_home() / "crews").glob("*.json"))


def voice_style(cfg: dict) -> str:
    style = (cfg.get("voice") or {}).get("style") or DEFAULTS["voice"]["style"]
    return PLAIN_STYLE if style == "plain" else style
