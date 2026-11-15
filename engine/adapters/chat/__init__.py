from __future__ import annotations

from engine.adapters.chat.slack import SlackChat


def make_chat(cfg: dict, call=None) -> SlackChat:
    return SlackChat(cfg, call=call)
