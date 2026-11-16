from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

from cli import doctor, install
from engine.lib import config, paths, state

DOCS = ("docs/setup-linear.md", "docs/setup-slack.md", "examples/slack-app-manifest.json")


def write_secret(path: Path, value: str) -> None:
    """Written 0400 into the secrets folder, which is open (0700) only for this write and 0500 again after."""
    state.atomic_write(path, value + "\n")


def read_secret(secret, prompt: str) -> str:
    for _ in range(3):
        value = secret(prompt).strip()
        if value:
            return value
    raise ValueError(f"no value given for: {prompt.strip(' :')}")


def replace_secret(crew_id: str, which: str, secret=getpass.getpass) -> None:
    cfg = config.load_crew(crew_id)[which]
    write_secret(Path(cfg["tokenFile"]), read_secret(secret, f"New {cfg['type']} token (hidden): "))


def ensure_dirs(crew_id: str) -> None:
    for d in ("worktrees", "tickets"):
        state.make_dir(paths.crew_state_dir(crew_id) / d)


def run_wizard(ask=input, out=print, secret=getpass.getpass, installer=install, deps=None) -> int:
    def q(prompt: str, default: str = "") -> str:
        return ask(f"{prompt}{f' [{default}]' if default else ''}: ").strip() or default

    out("Shopfloor setup. New crews start in dry run; this wizard never turns it off.")
    out(f"Linear and Slack setup: {DOCS[0]}, {DOCS[1]} (Slack app manifest: {DOCS[2]})")
    cid = q("Crew id", "demo")
    team, label = q("Linear team key"), q("Opt-in label", "shopfloor")
    states = {"todo": q("Linear state for new work", "Todo"), "inProgress": q("Linear in-progress state", "In Progress"),
              "review": q("Linear in-review state", "In Review")}
    channel, owner = q("Slack channel id"), q("Slack owner user id")
    repo = q("Repo name", "app")
    github, base = q("GitHub repo (owner/name)"), q("Base branch", "main")
    local = q("Local path to the checkout")
    try:
        every = int(q("Run every N hours", "3"))
    except ValueError:
        every = 0
    sched = {"start": q("First run (HH:MM)", "07:00"), "until": q("Last run (HH:MM)", "22:00"),
             "everyHours": every, "weekdaysOnly": q("Weekdays only (y/n)", "y") == "y"}

    secrets = paths.shopfloor_home() / "secrets"
    cfg = {
        "id": cid, "dryRun": True, "schedule": sched,
        "repos": {repo: {"github": github, "base": base, "path": local}},
        "protectedBranches": sorted({"main", "master", base}),
        "tracker": {"type": "linear", "tokenFile": str(secrets / f"{cid}-linear"),
                    "linear": {"team": team, "label": label, "states": states},
                    "mayMove": ["todo->inProgress", "inProgress->review"]},
        "chat": {"type": "slack", "tokenFile": str(secrets / f"{cid}-slack"),
                 "slack": {"channel": channel, "ownerUserId": owner}},
    }
    errs = config.validate(cfg)
    if errs:
        out("Config is invalid:\n- " + "\n- ".join(errs))
        return 1
    crew_file = paths.crew_config_path(cid)
    if crew_file.exists():
        out(f"{crew_file} already exists; edit it by hand or pick another crew id.")
        return 1
    write_secret(Path(cfg["tracker"]["tokenFile"]), read_secret(secret, "Linear API key (hidden): "))
    write_secret(Path(cfg["chat"]["tokenFile"]), read_secret(secret, "Slack bot token (hidden): "))
    state.atomic_write(crew_file, json.dumps(cfg, indent=2) + "\n")
    board_file = paths.shopfloor_home() / "board.json"
    board = json.loads(board_file.read_text()) if board_file.exists() else {}
    board["crews"] = [*(c for c in board.get("crews", []) if c != cid), cid]
    state.atomic_write(board_file, json.dumps(board, indent=2) + "\n")
    ensure_dirs(cid)
    out(f"Wrote {crew_file}")

    if doctor.report(doctor.check_all(cid, deps, during_init=True), out):
        out("Fix the failures above, then run: shopfloor install")
        return 1
    installer.install_board(config.load_board())
    installer.install_crew(config.load_crew(cid))
    out(f"Installed. Crew '{cid}' is in dry run: it reads and plans but writes nothing until you set dryRun to false.")
    return 0
