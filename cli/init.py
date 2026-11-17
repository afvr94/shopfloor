from __future__ import annotations

import getpass
import json
import os
import shlex
from pathlib import Path

from cli import doctor, install
from engine.adapters.tracker.base import settings
from engine.lib import config, paths, proc, state

DOCS = ("docs/setup-linear.md", "docs/setup-slack.md", "examples/slack-app-manifest.json")


def gh_login() -> str:
    return proc.run(["gh", "api", "user", "--jq", ".login"]).strip()


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
    if cfg["type"] == "github":
        raise ValueError(f"this crew's {which} uses your gh login; run gh auth login to change it")
    write_secret(Path(cfg["tokenFile"]), read_secret(secret, f"New {cfg['type']} token (hidden): "))


def label_argv(repo: str, name: str, color: str) -> list[str]:
    return ["gh", "label", "create", "--color", color, "--force", "--repo", repo, "--", name]


def create_label(repo: str, name: str, color: str) -> None:
    proc.run(label_argv(repo, name, color))


def ensure_dirs(crew_id: str) -> None:
    for d in ("worktrees", "tickets"):
        state.make_dir(paths.crew_state_dir(crew_id) / d)


def run_wizard(ask=input, out=print, secret=getpass.getpass, installer=install, deps=None, make_label=None,
               whoami=None) -> int:
    make_label = make_label or create_label
    whoami = whoami or gh_login

    def q(prompt: str, default: str = "") -> str:
        return ask(f"{prompt}{f' [{default}]' if default else ''}: ").strip() or default

    out("Shopfloor setup. New crews start in dry run; this wizard never turns it off.")
    cid = q("Crew id", "demo")
    ttype = q("Tracker (linear/github)", "linear")
    if ttype not in ("linear", "github"):
        out("Tracker must be linear or github.")
        return 1
    ctype = q("Chat (slack/github)", "github") if ttype == "github" else "slack"
    if ctype not in ("slack", "github"):
        out("Chat must be slack or github.")
        return 1
    tracker_doc = DOCS[0] if ttype == "linear" else "docs/setup-github-issues.md"
    if ctype == "github":
        out(f"Tracker and chat setup: {tracker_doc}, docs/setup-github-chat.md")
    else:
        out(f"Tracker and Slack setup: {tracker_doc}, {DOCS[1]} (Slack app manifest: {DOCS[2]})")
    repo = q("Repo name", "app")
    github, base = q("GitHub repo (owner/name)"), q("Base branch", "main")
    local = q("Local path to the checkout")
    label = q("Opt-in label", "shopfloor")
    if ttype == "github":
        tracker = {"type": "github", "github": {"repo": q("Issues repo (owner/name)", github), "label": label},
                   "mayMove": ["todo->inProgress", "inProgress->review"]}
        make_labels = q(f"Create the shopfloor labels in {tracker['github']['repo']} now? (y/n)", "y") == "y"
    else:
        team = q("Linear team key")
        states = {"todo": q("Linear state for new work", "Todo"), "inProgress": q("Linear in-progress state", "In Progress"),
                  "review": q("Linear in-review state", "In Review")}
        tracker = {"type": "linear", "tokenFile": str(paths.shopfloor_home() / "secrets" / f"{cid}-linear"),
                   "linear": {"team": team, "label": label, "states": states},
                   "mayMove": ["todo->inProgress", "inProgress->review"]}
    if ctype == "github":
        try:
            guess = whoami()
        except (proc.ProcError, OSError):
            guess = ""
        chat = {"type": "github", "github": {"owner": q("GitHub owner login", guess)}}
    else:
        chat = {"type": "slack", "tokenFile": str(paths.shopfloor_home() / "secrets" / f"{cid}-slack"),
                "slack": {"channel": q("Slack channel id"), "ownerUserId": q("Slack owner user id")}}
    try:
        every = int(q("Run every N hours", "3"))
    except ValueError:
        every = 0
    sched = {"start": q("First run (HH:MM)", "07:00"), "until": q("Last run (HH:MM)", "22:00"),
             "everyHours": every, "weekdaysOnly": q("Weekdays only (y/n)", "y") == "y"}

    cfg = {
        "id": cid, "dryRun": True, "schedule": sched,
        "repos": {repo: {"github": github, "base": base, "path": local}},
        "protectedBranches": sorted({"main", "master", base}),
        "tracker": tracker,
        "chat": chat,
    }
    errs = config.validate(cfg)
    if errs:
        out("Config is invalid:\n- " + "\n- ".join(errs))
        return 1
    crew_file = paths.crew_config_path(cid)
    if crew_file.exists():
        out(f"{crew_file} already exists; edit it by hand or pick another crew id.")
        return 1
    if ttype == "linear":
        write_secret(Path(cfg["tracker"]["tokenFile"]), read_secret(secret, "Linear API key (hidden): "))
    if ctype == "slack":
        write_secret(Path(chat["tokenFile"]), read_secret(secret, "Slack bot token (hidden): "))
    state.atomic_write(crew_file, json.dumps(cfg, indent=2) + "\n")
    board_file = paths.shopfloor_home() / "board.json"
    board = json.loads(board_file.read_text()) if board_file.exists() else {}
    board["crews"] = [*(c for c in board.get("crews", []) if c != cid), cid]
    state.atomic_write(board_file, json.dumps(board, indent=2) + "\n")
    ensure_dirs(cid)
    out(f"Wrote {crew_file}")
    if ttype == "github" and make_labels:
        gh = settings(config.load_crew(cid))
        for name, color in ((gh["label"], "0e8a16"), (gh["states"]["inProgress"], "fbca04"),
                            (gh["states"]["review"], "1d76db")):
            try:
                make_label(gh["repo"], name, color)
            except (proc.ProcError, OSError) as e:
                out(f"Could not create label {name}: {e}\n  run: {shlex.join(label_argv(gh['repo'], name, color))}")

    if doctor.report(doctor.check_all(cid, deps, during_init=True), out):
        out("Fix the failures above, then run: shopfloor install")
        return 1
    installer.install_board(config.load_board())
    installer.install_crew(config.load_crew(cid))
    out(f"Installed. Crew '{cid}' is in dry run: it reads and plans but writes nothing until you set dryRun to false.")
    return 0
