from __future__ import annotations

from decimal import Decimal

from engine.adapters.chat.base import ChatError, Reply, ThreadRef
from engine.adapters.tracker.base import Ticket
from engine.lib import config
from engine.lib.http import HttpError, request_json
from engine.lib.redact import redact

API = "https://slack.com/api/"


class SlackChat:
    def __init__(self, cfg: dict, call=None):
        self.cfg = cfg
        self.slack = cfg["chat"]["slack"]
        self.voice = cfg["voice"]["name"]
        self._token: str | None = None
        self.call = call or self._call

    def _call(self, method: str, params: dict) -> dict:
        if self._token is None:
            self._token = config.read_secret(self.cfg["chat"]["tokenFile"])
        extra = {"retries": 0} if method == "chat.postMessage" else {}  # never post a message twice
        try:
            resp = request_json("POST", API + method, headers={"Authorization": "Bearer " + self._token},
                                form=params, **extra)
        except HttpError as e:
            raise ChatError(redact(str(e))) from None
        if not resp.get("ok"):
            raise ChatError(f"slack {method} failed: {redact(str(resp.get('error', 'unknown_error')))}")
        return resp

    def _send(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        params = {"channel": channel, "text": text, "username": self.voice}
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self.call("chat.postMessage", params)["ts"]

    def open_thread(self, ticket: Ticket) -> ThreadRef:
        channel = self.slack["channel"]
        return ThreadRef(channel, self._send(channel, f"{ticket.key}: {ticket.title}\n{ticket.url}"))

    def post(self, thread: ThreadRef, text: str) -> str:
        return self._send(thread.channel, text, thread.ts)

    def ask(self, thread: ThreadRef, question: str, options: list[str], recommendation: int) -> str:
        lines = [question] + [f"{i}. {o}" for i, o in enumerate(options, 1)]
        lines.append(f"Recommended: {recommendation}. Reply with a number, or write your own answer.")
        return self.post(thread, "\n".join(lines))

    def poll(self, threads: dict[str, ThreadRef], since: dict[str, str]) -> list[Reply]:
        owner = self.slack["ownerUserId"]
        out: list[Reply] = []
        for key, th in threads.items():
            floor = since.get(key, th.ts)
            cursor = None
            while True:
                params = {"channel": th.channel, "ts": th.ts, "oldest": floor, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                resp = self.call("conversations.replies", params)
                for m in resp.get("messages", []):
                    if (m.get("user") == owner and not m.get("bot_id") and not m.get("subtype")
                            and m["ts"] != th.ts and Decimal(m["ts"]) > Decimal(floor)):
                        out.append(Reply(key, owner, m.get("text", ""), m["ts"]))
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        return sorted(out, key=lambda r: Decimal(r.ts))

    def auth_test(self) -> str:
        return self.call("auth.test", {})["user"]

    def channel_check(self) -> str | None:
        try:
            info = self.call("conversations.info", {"channel": self.slack["channel"]})
        except ChatError as e:
            return str(e)
        if not info.get("channel", {}).get("is_member"):
            return f"the bot is not a member of channel {self.slack['channel']}; invite it"
        return None
