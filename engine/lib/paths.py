from __future__ import annotations

import os
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_DIR.parent
SF_BIN = ENGINE_DIR / "bin"


def shopfloor_home() -> Path:
    return Path(os.environ.get("SHOPFLOOR_HOME") or "~/.shopfloor").expanduser()


def crew_config_path(crew_id: str) -> Path:
    return shopfloor_home() / "crews" / f"{crew_id}.json"


def crew_state_dir(crew_id: str) -> Path:
    d = shopfloor_home() / "state" / crew_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def ticket_dir(crew_id: str, key: str) -> Path:
    return crew_state_dir(crew_id) / "tickets" / key


def worktrees_dir(crew_id: str) -> Path:
    return crew_state_dir(crew_id) / "worktrees"


def logs_dir() -> Path:
    return shopfloor_home() / "logs"
