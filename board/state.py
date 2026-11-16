from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from board import sessions
from cli.schedule import run_times
from engine.lib import config, lessons, paths, runner, state
from engine.lib.redact import redact

TICK_LOCK = "tick.lock"
_TICKET_FIELDS = ("key", "title", "url", "stage", "prs", "branch", "fixRounds", "updatedAt")


def crew_dir(crew_id: str) -> Path:
    """A crew's state dir without creating it, so polling an idle crew writes nothing."""
    return paths.shopfloor_home() / "state" / paths._check(paths.CREW_ID_RE, crew_id, "crew id")


def tick_running(crew_id: str) -> bool:
    return state.read_lock(crew_dir(crew_id) / TICK_LOCK) is not None


def next_run(schedule: dict | None, now: datetime | None = None) -> str | None:
    if not schedule:
        return None
    now = now or datetime.now()
    try:
        times = run_times(schedule)
    except (KeyError, ValueError, AttributeError):
        return None
    for day in range(8):
        d = (now + timedelta(days=day)).replace(second=0, microsecond=0)
        if schedule.get("weekdaysOnly") and d.weekday() > 4:
            continue
        for h, m in times:
            at = d.replace(hour=h, minute=m)
            if at > now:
                return at.isoformat(timespec="minutes")
    return None


def crew_state(crew_id: str) -> dict:
    try:
        cfg = config.load_crew(crew_id)
    except (config.ConfigError, ValueError) as e:
        return {"id": crew_id, "error": redact(str(e))}
    out = {
        "id": crew_id,
        "name": cfg["voice"]["name"],
        "voice": cfg["voice"],
        "dryRun": cfg["dryRun"],
        "paused": cfg["paused"],
        "gitFlow": {"type": cfg["gitFlow"]["type"]},
        "running": False, "building": None, "nextRun": next_run(cfg.get("schedule")),
        "tickets": [], "questions": [], "runs": [], "lessons": [], "error": None,
    }
    if not crew_dir(crew_id).is_dir():
        return out
    out["running"] = tick_running(crew_id)
    out["building"] = runner.status(crew_id)
    out["tickets"] = [{k: t[k] for k in _TICKET_FIELDS} for t in state.list_tickets(crew_id)]
    out["questions"] = [{"key": t["key"], "title": t["title"], "question": t["question"]}
                        for t in state.list_tickets(crew_id) if t["stage"] == "needs_you"]
    out["runs"] = state.recent_runs(crew_id, 60)
    try:
        out["lessons"] = lessons.show(crew_id)
    except lessons.LessonError:
        pass
    return out


def board_state() -> dict:
    try:
        crews = [crew_state(c) for c in config.list_crews()]
    except config.ConfigError as e:
        return {"crews": [], "sessions": [], "error": redact(str(e))}
    try:
        found = sessions.crew_sessions(Path.home() / ".claude" / "projects", paths.shopfloor_home())
    except OSError:
        found = []
    return {"crews": crews, "sessions": found, "error": None}


def briefing_data(crew_id: str, key: str) -> dict | None:
    try:
        d = json.loads((crew_dir(crew_id) / "tickets" / paths._check(paths.TICKET_KEY_RE, key, "ticket key")
                        / "briefing.json").read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None
