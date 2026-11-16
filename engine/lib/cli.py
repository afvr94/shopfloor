from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys

from engine.adapters.chat import make_chat
from engine.adapters.chat.base import ChatError, ThreadRef
from engine.adapters.gitflow import make_gitflow
from engine.adapters.gitflow.base import GitFlowError, pr_info
from engine.adapters.tracker import make_tracker
from engine.adapters.tracker.base import TrackerError, state_key
from engine.lib import briefing, config, lessons, paths, state, worktrees
from engine.lib.commands import CommandQueue, CommandQueueError
from engine.lib.proc import ProcError
from engine.lib.redact import redact

FACTORIES = {"tracker": make_tracker, "chat": make_chat, "gitflow": make_gitflow}

WRITE_COMMANDS = {("tracker", "transition"), ("tracker", "comment"), ("tracker", "link-pr"),
                  ("ship", ""), ("merge", "")}

_ERRORS = (TrackerError, ChatError, GitFlowError, briefing.BriefingError, lessons.LessonError,
           config.ConfigError, CommandQueueError, ProcError, ValueError, OSError)


class Refused(Exception):
    pass


class Usage(Exception):
    pass


def sign(body: str, voice_name: str) -> str:
    return f"{body}\n\n— {voice_name}, a Shopfloor crew"


def _out(obj) -> None:
    print(json.dumps(obj, indent=2))


def _dry(cfg: dict, key: str | None, msg: str) -> bool:
    if not cfg["dryRun"]:
        return False
    print(f"dry-run: would {msg}")
    state.log_run(cfg["id"], key, "dry-run", msg)
    return True


def _repo(cfg: dict, name: str) -> dict:
    if name not in cfg["repos"]:
        raise Refused(f"unknown repo {name!r}; valid: {', '.join(cfg['repos'])}")
    return cfg["repos"][name]


def _thread(cfg: dict, key: str) -> ThreadRef:
    t = state.load_ticket(cfg["id"], key)["thread"]
    if not t:
        raise Refused(f"no thread for {key}; run sf chat open {key}")
    return ThreadRef.from_dict(t)


def _make(kind: str, cfg: dict):
    return FACTORIES[kind](cfg)


def h_config(a, cfg):
    _out({"id": cfg["id"], "name": cfg.get("name", cfg["id"]),
          "voice": {"name": cfg["voice"]["name"], "style": config.voice_style(cfg)},
          "dryRun": cfg["dryRun"], "paused": cfg["paused"], "autoRecommend": cfg["autoRecommend"],
          "limits": cfg["limits"],
          "repos": {n: {"github": r["github"], "base": r["base"]} for n, r in cfg["repos"].items()},
          "gitFlow": {"type": cfg["gitFlow"]["type"]}, "policyPacks": cfg["policyPacks"]})


def h_candidates(a, cfg):
    _out([dataclasses.asdict(t) for t in _make("tracker", cfg).list_candidates()])


def h_get(a, cfg):
    _out(dataclasses.asdict(_make("tracker", cfg).get(a.key)))


def h_transition(a, cfg):
    tracker = _make("tracker", cfg)
    if cfg["dryRun"]:
        cur = tracker.get(a.key).state
        _dry(cfg, a.key, f"move {a.key} {state_key(cfg, cur) or cur}->{a.to_key}")
        return
    tracker.transition(a.key, a.to_key)
    state.log_run(cfg["id"], a.key, "transition", f"to {a.to_key}")
    print(f"moved {a.key} to {a.to_key}")


def h_comment(a, cfg):
    if _dry(cfg, a.key, f"comment on {a.key}"):
        return
    body = sign(open(a.body_file).read(), cfg["voice"]["name"])
    tracker = _make("tracker", cfg)
    if a.update:
        tracker.update_comment(a.key, a.update, body)
        print(a.update)
    else:
        print(tracker.comment(a.key, body))


def h_link_pr(a, cfg):
    if not _dry(cfg, a.key, f"link {a.url} to {a.key}"):
        _make("tracker", cfg).link_pr(a.key, a.url)
        print(f"linked {a.url} to {a.key}")


def h_chat_open(a, cfg):
    t = state.load_ticket(cfg["id"], a.key)
    if t["thread"]:
        return _out(t["thread"])
    ticket = _make("tracker", cfg).get(a.key)
    thread = _make("chat", cfg).open_thread(ticket).to_dict()
    extra = {k: v for k, v in (("title", ticket.title), ("url", ticket.url)) if not t[k]}
    state.save_ticket(cfg["id"], a.key, thread=thread, **extra)
    _out(thread)


def h_chat_post(a, cfg):
    print(_make("chat", cfg).post(_thread(cfg, a.key), open(a.text_file).read()))


def h_chat_ask(a, cfg):
    if not 2 <= len(a.option) <= 3 or not 1 <= a.recommend <= len(a.option):
        raise Usage("need 2-3 --option and --recommend within 1..N")
    ref = _make("chat", cfg).ask(_thread(cfg, a.key), a.question, a.option, a.recommend)
    state.save_ticket(cfg["id"], a.key, question=a.question, stage="needs_you")
    print(ref)


def h_cmd_pending(a, cfg):
    _out(CommandQueue(paths.crew_state_dir(cfg["id"])).pending())


def h_cmd_ack(a, cfg):
    CommandQueue(paths.crew_state_dir(cfg["id"])).ack(a.seq)


def h_ticket_list(a, cfg):
    _out(state.list_tickets(cfg["id"]))


def h_ticket_show(a, cfg):
    d = paths.ticket_dir(cfg["id"], a.key)
    d.mkdir(parents=True, exist_ok=True)
    _out({**state.load_ticket(cfg["id"], a.key), "dir": str(d)})


def h_ticket_set(a, cfg):
    changes = {}
    for f in a.field:
        name, sep, value = f.partition("=")
        if not sep:
            raise Usage(f"--field needs name=value, got {f!r}")
        changes[name] = json.loads(value) if name in ("prs", "thread") else int(value) if name == "fixRounds" else value
    if a.stage:
        changes["stage"] = a.stage
    if not changes:
        raise Usage("nothing to set")
    _out(state.save_ticket(cfg["id"], a.key, **changes))


def h_lessons_show(a, cfg):
    _out(lessons.show(cfg["id"], a.area))


def h_lessons_add(a, cfg):
    _out(lessons.add(cfg["id"], a.area, a.text, a.source))


def h_lessons_bump(a, cfg):
    _out(lessons.bump(cfg["id"], a.id, a.source))


def h_lessons_forget(a, cfg):
    _out(lessons.forget(cfg["id"], a.id))


def h_lessons_edit(a, cfg):
    _out(lessons.edit(cfg["id"], a.id, a.text, a.source))


def h_briefing_write(a, cfg):
    with open(a.file) as f:
        print(briefing.write_briefing(cfg["id"], a.key, json.load(f)))


def h_briefing_url(a, cfg):
    print(briefing.briefing_url(cfg["id"], a.key))


def h_wt_prepare(a, cfg):
    _repo(cfg, a.repo)
    t = state.load_ticket(cfg["id"], a.key)
    branch = a.branch or t["branch"] or worktrees.branch_name(a.key, t["title"])
    wt = worktrees.prepare(cfg, a.key, a.repo, branch)
    state.save_ticket(cfg["id"], a.key, branch=branch)
    print(wt)


def h_wt_remove(a, cfg):
    worktrees.remove(cfg, a.key)


def h_ship(a, cfg):
    _repo(cfg, a.repo)
    if _dry(cfg, a.key, f"open a PR for {a.key} in {a.repo}"):
        return
    wt = paths.worktrees_dir(cfg["id"]) / a.key / a.repo
    if not wt.is_dir():
        raise Refused(f"no worktree for {a.key} in {a.repo}; run sf worktree prepare")
    url = _make("gitflow", cfg).ship(a.key, a.repo, wt, a.title, open(a.body_file).read())
    m = re.search(r"/pull/(\d+)", url)
    if not m:
        raise Refused(f"could not read a PR number from {url}")
    prs = [p for p in state.load_ticket(cfg["id"], a.key)["prs"] if p.get("url") != url]
    state.save_ticket(cfg["id"], a.key, prs=prs + [{"repo": a.repo, "number": int(m.group(1)), "url": url}])
    print(url)


def h_merge(a, cfg):
    repo = _repo(cfg, a.repo)
    if cfg["paused"]:
        raise Refused("paused")
    if _dry(cfg, a.key, f"merge {a.repo}#{a.pr_number}"):
        return
    flow = _make("gitflow", cfg)
    pr = pr_info(repo["github"], a.pr_number)
    ok, why = flow.can_merge(a.repo, pr)
    if not ok:
        raise Refused(why)
    print(flow.merge(a.key, a.repo, pr))


def h_runs_log(a, cfg):
    state.log_run(cfg["id"], a.key, a.result, a.note)


def h_build(a, cfg):
    try:
        from engine.lib import runner
    except ImportError:
        raise Refused("build runner not available") from None
    if a.sub == "start":
        runner.start(cfg["id"], a.keys)
    else:
        _out(runner.status(cfg["id"]))


def h_pause(a, cfg):
    p = paths.crew_config_path(cfg["id"])
    raw = json.loads(p.read_text())
    raw["paused"] = a.cmd == "pause"
    state.atomic_write(p, json.dumps(raw, indent=2) + "\n")
    print("paused" if raw["paused"] else "resumed")


def _spec():
    k = (("key",), {})
    return [
        ("config", "", h_config, []),
        ("tracker", "candidates", h_candidates, []),
        ("tracker", "get", h_get, [k]),
        ("tracker", "transition", h_transition, [k, (("to_key",), {})]),
        ("tracker", "comment", h_comment, [k, (("--body-file",), {"required": True}), (("--update",), {})]),
        ("tracker", "link-pr", h_link_pr, [k, (("url",), {})]),
        ("chat", "open", h_chat_open, [k]),
        ("chat", "post", h_chat_post, [k, (("--text-file",), {"required": True})]),
        ("chat", "ask", h_chat_ask, [k, (("--question",), {"required": True}),
                                     (("--option",), {"action": "append", "default": []}),
                                     (("--recommend",), {"type": int, "required": True})]),
        ("commands", "pending", h_cmd_pending, []),
        ("commands", "ack", h_cmd_ack, [(("seq",), {"type": int})]),
        ("ticket", "list", h_ticket_list, []),
        ("ticket", "show", h_ticket_show, [k]),
        ("ticket", "set", h_ticket_set, [k, (("--stage",), {}), (("--field",), {"action": "append", "default": []})]),
        ("lessons", "show", h_lessons_show, [(("--area",), {})]),
        ("lessons", "add", h_lessons_add, [(("--area",), {"required": True}), (("--text",), {"required": True}),
                                           (("--source",), {"required": True})]),
        ("lessons", "bump", h_lessons_bump, [(("id",), {}), (("--source",), {"required": True})]),
        ("lessons", "forget", h_lessons_forget, [(("id",), {})]),
        ("lessons", "edit", h_lessons_edit, [(("id",), {}), (("--text",), {"required": True}),
                                             (("--source",), {"required": True})]),
        ("briefing", "write", h_briefing_write, [k, (("file",), {})]),
        ("briefing", "url", h_briefing_url, [k]),
        ("worktree", "prepare", h_wt_prepare, [k, (("repo",), {}), (("--branch",), {})]),
        ("worktree", "remove", h_wt_remove, [k]),
        ("ship", "", h_ship, [k, (("repo",), {}), (("--title",), {"required": True}),
                              (("--body-file",), {"required": True})]),
        ("merge", "", h_merge, [k, (("repo",), {}), (("pr_number",), {"type": int})]),
        ("runs", "log", h_runs_log, [k, (("result",), {}), (("--note",), {"default": ""})]),
        ("build", "start", h_build, [(("keys",), {"nargs": "+"})]),
        ("build", "status", h_build, []),
        ("pause", "", h_pause, []),
        ("resume", "", h_pause, []),
    ]


def _parser():
    p = argparse.ArgumentParser(prog="sf")
    top = p.add_subparsers(dest="cmd", required=True)
    groups: dict = {}
    for cmd, sub, fn, args in _spec():
        if sub:
            if cmd not in groups:
                groups[cmd] = top.add_parser(cmd).add_subparsers(dest="sub", required=True)
            sp = groups[cmd].add_parser(sub)
        else:
            sp = top.add_parser(cmd)
            sp.set_defaults(sub="")
        for names, kw in args:
            sp.add_argument(*names, **kw)
        sp.set_defaults(fn=fn)
    return p


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    crew = os.environ.get("SHOPFLOOR_CREW")
    if not crew:
        print("SHOPFLOOR_CREW is not set", file=sys.stderr)
        return 2
    try:
        a.fn(a, config.load_crew(crew))
    except Usage as e:
        print(redact(str(e)), file=sys.stderr)
        return 2
    except (Refused, *_ERRORS) as e:
        print(redact(str(e)), file=sys.stderr)
        return 1
    return 0
