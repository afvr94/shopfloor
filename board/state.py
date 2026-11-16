from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from board import sessions
from cli.schedule import run_times
from engine.lib import briefing, config, lessons, paths, runner, signing, state
from engine.lib.redact import redact

_TICKET_FIELDS = ("key", "title", "url", "stage", "prs", "branch", "fixRounds", "updatedAt", "tampered")


def tick_running(crew_id: str) -> bool:
    return state.read_lock(paths.tick_lock(crew_id)) is not None


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
        "tickets": [], "questions": [], "runs": [], "lessons": [], "tampered": [], "error": None,
    }
    if not paths.crew_dir(crew_id).is_dir():
        return out
    out["running"] = tick_running(crew_id)
    out["building"] = runner.status(crew_id)
    tickets = state.list_tickets(crew_id)
    out["tickets"] = [{k: t.get(k, False) for k in _TICKET_FIELDS} for t in tickets]
    out["tampered"] = [t["question"] for t in tickets if t.get("tampered")]
    out["questions"] = [{"key": t["key"], "title": t["title"], "question": t["question"]}
                        for t in tickets if t["stage"] == "needs_you"]
    out["runs"] = state.recent_runs(crew_id, 60)
    try:
        out["lessons"] = lessons.show(crew_id)
    except lessons.LessonError as e:
        if "tampered" in str(e):
            out["tampered"].append(redact(str(e)))
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
    path = paths.ticket_state_dir(crew_id, key) / "briefing.json"
    try:
        return signing.unseal(briefing.scope(crew_id, key), json.loads(path.read_text()), str(path))
    except signing.Tampered:
        state.report_tampered(crew_id, key, path)
    except (OSError, ValueError):
        pass
    return None
