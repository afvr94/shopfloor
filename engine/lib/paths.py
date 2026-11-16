from __future__ import annotations

import os
import re
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_DIR.parent
SF_BIN = ENGINE_DIR / "bin"

CREW_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
TICKET_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,19}")


def check(pattern: re.Pattern, value: object, what: str) -> str:
    if not (isinstance(value, str) and pattern.fullmatch(value)):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def shopfloor_home() -> Path:
    return Path(os.environ.get("SHOPFLOOR_HOME") or "~/.shopfloor").expanduser()


def crew_config_path(crew_id: str) -> Path:
    return shopfloor_home() / "crews" / f"{check(CREW_ID_RE, crew_id, 'crew id')}.json"


def crew_dir(crew_id: str) -> Path:
    """A crew's state dir path; unlike crew_state_dir it never creates it."""
    return shopfloor_home() / "state" / check(CREW_ID_RE, crew_id, "crew id")


def crew_state_dir(crew_id: str) -> Path:
    d = crew_dir(crew_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def tick_lock(crew_id: str) -> Path:
    return crew_dir(crew_id) / "lock"


def build_lock(crew_id: str) -> Path:
    return crew_dir(crew_id) / "build.lock"


def ticket_dir(crew_id: str, key: str) -> Path:
    check(TICKET_KEY_RE, key, "ticket key")
    return crew_state_dir(crew_id) / "tickets" / key


def worktrees_dir(crew_id: str) -> Path:
    return crew_state_dir(crew_id) / "worktrees"


def logs_dir() -> Path:
    return shopfloor_home() / "logs"
