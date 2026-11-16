from __future__ import annotations


def run_times(schedule: dict) -> list[tuple[int, int]]:
    """start..until inclusive, stepping everyHours."""
    sh, sm = (int(x) for x in schedule["start"].split(":"))
    uh, um = (int(x) for x in schedule["until"].split(":"))
    every = schedule["everyHours"]
    if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= 24:
        raise ValueError("schedule.everyHours must be an integer from 1 to 24")
    start, until = sh * 60 + sm, uh * 60 + um
    if until <= start:
        raise ValueError("schedule.until must be later than schedule.start")
    return [divmod(m, 60) for m in range(start, until + 1, every * 60)]


def launchd_intervals(schedule: dict) -> list[dict]:
    days = [1, 2, 3, 4, 5] if schedule.get("weekdaysOnly") else [None]
    return [
        {"Hour": h, "Minute": m, **({} if d is None else {"Weekday": d})}
        for d in days
        for h, m in run_times(schedule)
    ]


def systemd_oncalendar(schedule: dict) -> str:
    times = run_times(schedule)
    minutes = {m for _, m in times}
    if len(minutes) != 1:
        # shortcut: one OnCalendar line, so start's minute is used for every run
        times = [(h, times[0][1]) for h, _ in times]
    hours = ",".join(f"{h:02d}" for h, _ in times)
    day = "Mon..Fri " if schedule.get("weekdaysOnly") else ""
    return f"{day}*-*-* {hours}:{times[0][1]:02d}:00"
