from __future__ import annotations

from engine.adapters.chat.github import GitHubChat
from engine.adapters.chat.slack import SlackChat


def make_chat(cfg: dict, call=None, run=None) -> SlackChat | GitHubChat:
    if cfg["chat"]["type"] == "github":
        return GitHubChat(cfg, run=run)
    return SlackChat(cfg, call=call)
