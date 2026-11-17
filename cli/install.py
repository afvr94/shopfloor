from __future__ import annotations

import os
import plistlib
import sys
import time
from pathlib import Path
from string import Template

from cli import schedule
from engine.lib import config, paths, proc

BOARD_SERVICE = Template("""[Unit]
Description=Shopfloor board

[Service]
ExecStart=$exec
Environment=$env
Restart=always
StandardOutput=append:$log
StandardError=append:$log

[Install]
WantedBy=default.target
""")

CREW_SERVICE = Template("""[Unit]
Description=Shopfloor crew $id

[Service]
Type=oneshot
ExecStart=$exec
Environment=$env
StandardOutput=append:$log
StandardError=append:$log
""")

CREW_TIMER = Template("""[Unit]
Description=Shopfloor crew $id schedule

[Timer]
OnCalendar=$calendar
Persistent=false

[Install]
WantedBy=timers.target
""")


def _platform() -> str:
    if sys.platform not in ("darwin", "linux"):
        raise RuntimeError(f"unsupported platform: {sys.platform}")
    return sys.platform


def _home() -> Path:
    return Path(os.environ.get("HOME") or "~").expanduser()


def _unit_dir() -> Path:
    if _platform() == "darwin":
        return _home() / "Library" / "LaunchAgents"
    return _home() / ".config" / "systemd" / "user"


def _prefix(board_cfg: dict) -> str:
    return board_cfg.get("labelPrefix") or "dev.shopfloor"


def _env() -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if os.environ.get("SHOPFLOOR_HOME"):
        env["SHOPFLOOR_HOME"] = os.environ["SHOPFLOOR_HOME"]
    return env


def _bin(*args: str) -> list[str]:
    return [sys.executable, str(paths.REPO_ROOT / "bin" / "shopfloor"), *args]


def _q(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("unit values must not contain newlines")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def _systemd_vars(argv: list[str], log: Path) -> dict:
    return {
        "exec": " ".join(_q(a) for a in argv),
        "env": " ".join(_q(f"{k}={v}") for k, v in _env().items()),
        "log": _q(str(log))[1:-1],
    }


def _plist(label: str, argv: list[str], log: Path, extra: dict) -> bytes:
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": argv,
        "EnvironmentVariables": _env(),
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        **extra,
    })


def _write(files: dict[Path, str | bytes]) -> list[Path]:
    for p, content in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    return list(files)


def _activate(unit_files: list[Path], runner, systemd_unit: str, label: str) -> None:
    if _platform() == "darwin":
        target = f"gui/{os.getuid()}"
        try:
            runner(["launchctl", "bootout", f"{target}/{label}"])
        except proc.ProcError:
            pass
        # bootout returns before launchd has torn a running KeepAlive service down, and bootstrapping over it fails
        for attempt in range(10):
            try:
                runner(["launchctl", "bootstrap", target, str(unit_files[0])])
                break
            except proc.ProcError:
                if attempt == 9:
                    raise
                time.sleep(0.5)
    else:
        runner(["systemctl", "--user", "daemon-reload"])
        runner(["systemctl", "--user", "enable", "--now", systemd_unit])


def _install(name: str, argv: list[str], extra_plist: dict, calendar, runner, prefix: str) -> list[Path]:
    d = _unit_dir()
    log = paths.logs_dir() / f"{name}.log"
    if _platform() == "darwin":
        label = f"{prefix}.{name.replace('crew-', 'crew.', 1)}"
        files = _write({d / f"{label}.plist": _plist(label, argv, log, extra_plist)})
        _activate(files, runner, "", label)
        return files
    unit = f"shopfloor-{name}"
    v = _systemd_vars(argv, log)
    if calendar is None:
        files = _write({d / f"{unit}.service": BOARD_SERVICE.substitute(v)})
        _activate(files, runner, f"{unit}.service", "")
    else:
        crew = {**v, "id": name[len("crew-"):]}
        files = _write({
            d / f"{unit}.service": CREW_SERVICE.substitute(crew),
            d / f"{unit}.timer": CREW_TIMER.substitute({**crew, "calendar": calendar}),
        })
        _activate(files, runner, f"{unit}.timer", "")
    return files


def install_board(board_cfg: dict, runner=None) -> list[Path]:
    runner = runner or proc.run  # looked up per call so tests can patch it
    return _install("board", _bin("board"), {"KeepAlive": True, "RunAtLoad": True},
                    None, runner, _prefix(board_cfg))


def install_crew(cfg: dict, runner=None) -> list[Path]:
    runner = runner or proc.run
    sched = cfg["schedule"]
    crew_id = cfg["id"]
    paths.crew_config_path(crew_id)  # validates the id
    return _install(
        f"crew-{crew_id}", _bin("run", crew_id),
        {"StartCalendarInterval": schedule.launchd_intervals(sched)},
        schedule.systemd_oncalendar(sched), runner, _prefix(config.load_board()),
    )


def unit_paths() -> list[Path]:
    """Installed Shopfloor unit files; nothing else in the unit dir is ever listed."""
    d = _unit_dir()
    if not d.is_dir():
        return []
    if _platform() == "darwin":
        pre = _prefix(config.load_board())
        pats = [f"{pre}.board.plist", f"{pre}.crew.*.plist"]
    else:
        pats = ["shopfloor-board.service", "shopfloor-crew-*.service", "shopfloor-crew-*.timer"]
    return sorted({p for pat in pats for p in d.glob(pat)})


def uninstall(runner=None) -> list[Path]:
    runner = runner or proc.run
    found = unit_paths()
    for p in found:
        try:
            if _platform() == "darwin":
                runner(["launchctl", "bootout", f"gui/{os.getuid()}/{p.stem}"])
            elif p.suffix == ".timer" or p.name == "shopfloor-board.service":
                runner(["systemctl", "--user", "disable", "--now", p.name])
        except proc.ProcError:
            pass
    for p in found:
        p.unlink()
    if found and _platform() == "linux":
        try:
            runner(["systemctl", "--user", "daemon-reload"])
        except proc.ProcError:
            pass
    return found
