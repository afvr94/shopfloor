from __future__ import annotations

from dataclasses import dataclass


class TrackerError(Exception):
    pass


@dataclass
class Ticket:
    key: str
    id: str
    title: str
    body: str
    labels: list[str]
    state: str
    url: str
    assignee: str | None
    relations: list[str]
    created_at: str


def state_key(cfg: dict, state_name: str) -> str | None:
    for key, name in cfg["tracker"]["linear"]["states"].items():
        if name == state_name:
            return key
    return None


def check_move(cfg: dict, from_key: str, to_key: str) -> None:
    if f"{from_key}->{to_key}" not in cfg["tracker"].get("mayMove", []):
        raise TrackerError(f"move {from_key}->{to_key} is not in tracker.mayMove")
