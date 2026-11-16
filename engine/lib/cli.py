from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import re
import sys
import tempfile

from engine.adapters.chat import make_chat
from engine.adapters.chat.base import ChatError, ThreadRef
from engine.adapters.gitflow import base as flowbase
from engine.adapters.gitflow import make_gitflow
from engine.adapters.gitflow.base import GitFlowError, pr_info
from engine.adapters.tracker import make_tracker
from engine.adapters.tracker.base import TrackerError, state_key
from engine.lib import briefing, config, crewfs, gitsafe, lessons, paths, state, transport, worktrees
from engine.lib.commands import CommandQueue, CommandQueueError
from engine.lib.proc import ProcError
from engine.lib.redact import redact

FACTORIES = {"tracker": make_tracker, "chat": make_chat, "gitflow": make_gitflow}

WRITE_COMMANDS = {("tracker", "transition"), ("tracker", "comment"), ("tracker", "link-pr"),
                  ("ship", ""), ("merge", ""), ("push", ""), ("pr", "reply")}

_ERRORS = (TrackerError, ChatError, GitFlowError, briefing.BriefingError, lessons.LessonError,
           config.ConfigError, CommandQueueError, ProcError, gitsafe.CrewWritable, transport.TransportError,
           ValueError, OSError)


class Refused(Exception):
    pass


class Usage(Exception):
    pass


def sign(body: str, voice_name: str) -> str:
    return f"{body}\n\n— {voice_name}" + ("" if voice_name == "Shopfloor" else ", a Shopfloor crew")


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


def _under(p: str, roots) -> bool:
    p = p.casefold()  # macOS file systems ignore case
    return any(p == r.casefold() or p.startswith(r.casefold().rstrip("/") + "/") for r in roots)


def _write_roots(cfg: dict) -> list:
    return [paths.worktrees_dir(cfg["id"]), paths.tickets_dir(cfg["id"]), tempfile.gettempdir(), "/tmp"]


def _crew_file(cfg: dict, key: str, path: str) -> str:
    """A file the crew named (absolute, or relative to the ticket folder), read only from inside its write roots,
    without following any link, and no secret."""
    root, rel = crewfs.locate(_write_roots(cfg), path, base=paths.ticket_dir(cfg["id"], key))
    if _under(os.path.join(os.path.realpath(root), rel), [os.path.realpath(x) for x in config.secret_paths(cfg)]):
        raise Refused(f"sf does not read secret files: {path}")
    return crewfs.read_bytes(root, rel).decode()


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
          "gitFlow": {"type": cfg["gitFlow"]["type"]}, "policyPacks": cfg["policyPacks"],
          "tracker": {"label": _lin(cfg)["label"], "holdLabels": _lin(cfg).get("holdLabels", [])}})


def _lin(cfg: dict) -> dict:
    return cfg["tracker"]["linear"]


def _eligible(cfg: dict, labels: list[str]) -> bool:
    have = {l.lower() for l in labels}
    return _lin(cfg)["label"].lower() in have and not have & {h.lower() for h in _lin(cfg).get("holdLabels", [])}


def h_candidates(a, cfg):
    """Only tickets the crew has never staged: in dry run nothing moves in the tracker, so state is the claim."""
    known = {t["key"] for t in state.list_tickets(cfg["id"]) if t["stage"]}
    _out([dataclasses.asdict(t) for t in _make("tracker", cfg).list_candidates() if t.key not in known])


def h_get(a, cfg):
    t = _make("tracker", cfg).get(a.key)
    _out({**dataclasses.asdict(t), "eligible": _eligible(cfg, t.labels)})


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
    body = sign(redact(_crew_file(cfg, a.key, a.body_file)), cfg["voice"]["name"])
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
    print(_make("chat", cfg).post(_thread(cfg, a.key), redact(_crew_file(cfg, a.key, a.text_file))))


def h_chat_ask(a, cfg):
    if not 2 <= len(a.option) <= 3 or not 1 <= a.recommend <= len(a.option):
        raise Usage("need 2-3 --option and --recommend within 1..N")
    question, options = redact(a.question), [redact(o) for o in a.option]
    ref = _make("chat", cfg).ask(_thread(cfg, a.key), question, options, a.recommend)
    state.save_ticket(cfg["id"], a.key, question=question, stage="needs_you")
    print(ref)


def h_cmd_pending(a, cfg):
    _out(CommandQueue(paths.crew_state_dir(cfg["id"])).pending())


def h_cmd_ack(a, cfg):
    CommandQueue(paths.crew_state_dir(cfg["id"])).ack(a.seq)


def h_ticket_list(a, cfg):
    _out(state.list_tickets(cfg["id"]))


def h_ticket_show(a, cfg):
    with crewfs.folder(paths.tickets_dir(cfg["id"]), paths.check(paths.TICKET_KEY_RE, a.key, "ticket key"),
                       create=True):
        pass
    _out({**state.load_ticket(cfg["id"], a.key), "dir": str(paths.ticket_dir(cfg["id"], a.key))})


def h_ticket_set(a, cfg):
    changes = {}
    for f in a.field:
        name, sep, value = f.partition("=")
        if not sep:
            raise Usage(f"--field needs name=value, got {f!r}")
        if name == "branch":
            raise Refused("sf picks the ticket branch at sf worktree prepare; read it from sf ticket show")
        if name == "prs":
            raise Refused("sf ship records the ticket's PRs; read them from sf ticket show")
        changes[name] = json.loads(value) if name == "thread" else int(value) if name == "fixRounds" else value
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
    print(briefing.write_briefing(cfg["id"], a.key, json.loads(_crew_file(cfg, a.key, a.file))))


def h_briefing_url(a, cfg):
    print(briefing.briefing_url(cfg["id"], a.key))


def h_wt_prepare(a, cfg):
    """The branch is sf's: recorded at the first prepare from the key and title, and only ever read back."""
    _repo(cfg, a.repo)
    t = state.load_ticket(cfg["id"], a.key)
    branch = t["branch"] or worktrees.branch_name(a.key, t["title"])
    if not t["branch"]:
        state.save_ticket(cfg["id"], a.key, branch=branch)
    print(worktrees.prepare(cfg, a.key, a.repo, branch))


def h_wt_remove(a, cfg):
    worktrees.remove(cfg, a.key)


def h_ship(a, cfg):
    _repo(cfg, a.repo)
    if _dry(cfg, a.key, f"open a PR for {a.key} in {a.repo}"):
        return
    url = _make("gitflow", cfg).ship(a.key, a.repo, a.title, redact(_crew_file(cfg, a.key, a.body_file)))
    m = re.search(r"/pull/(\d+)", url)
    if not m:
        raise Refused(f"could not read a PR number from {url}")
    prs = [p for p in state.load_ticket(cfg["id"], a.key)["prs"] if p.get("url") != url]
    state.save_ticket(cfg["id"], a.key, prs=prs + [{"repo": a.repo, "number": int(m.group(1)), "url": url}])
    print(url)


def h_push(a, cfg):
    """Push the ticket branch the crew bundled into <dir>/out-<repo>.bundle."""
    _repo(cfg, a.repo)
    if _dry(cfg, a.key, f"push {a.key}'s branch in {a.repo}"):
        return
    branch = _make("gitflow", cfg).push(a.key, a.repo)
    state.log_run(cfg["id"], a.key, "pushed", branch)
    print(f"pushed {branch}")


def h_sync(a, cfg):
    """Fetch origin outside the sandbox and hand the result to the crew as <dir>/in-<repo>.bundle."""
    base = _repo(cfg, a.repo)["base"]
    branch = state.load_ticket(cfg["id"], a.key)["branch"]
    if not branch:
        raise Refused(f"no branch recorded for {a.key}; run sf worktree prepare")
    transport.update(cfg, a.repo)
    with transport.scratch(cfg["id"]) as tmp:
        p = transport.publish(transport.bundle_in(cfg, a.repo, branch, tmp / "in.bundle"), cfg["id"], a.key,
                              f"in-{a.repo}.bundle")
    print(f"wrote {p}\nin your clone run: git fetch {p} '+refs/heads/*:refs/remotes/origin/*'"
          f" then git rebase origin/{base}")


FAILED_CHECKS = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def _checks(rollup: list[dict]) -> dict:
    def st(c):
        return c.get("conclusion") or c.get("state") or c.get("status")
    failing = [{"name": c.get("name") or c.get("context"), "url": c.get("detailsUrl") or c.get("targetUrl")}
               for c in rollup if st(c) in FAILED_CHECKS]
    return {"green": flowbase.checks_green(rollup), "total": len(rollup), "failing": failing,
            "pending": sum(st(c) not in FAILED_CHECKS | flowbase.GREEN for c in rollup)}


def _comments(gh: str, number: int, view: dict, since: str) -> list[dict]:
    # shortcut: the first 100 inline comments only; page through them if reviews ever run longer
    inline = json.loads(flowbase.GH(["api", f"repos/{gh}/pulls/{number}/comments?per_page=100"]) or "[]")
    rows = [{"kind": "inline", "author": (c.get("user") or {}).get("login"), "body": c.get("body") or "",
             "path": c.get("path"), "line": c.get("line") or c.get("original_line"), "at": c.get("created_at")}
            for c in inline]
    rows += [{"kind": "comment", "author": (c.get("author") or {}).get("login"), "body": c.get("body") or "",
              "at": c.get("createdAt")} for c in view.get("comments") or []]
    rows += [{"kind": "review", "author": (r.get("author") or {}).get("login"), "body": r.get("body") or "",
              "state": r.get("state"), "at": r.get("submittedAt")} for r in view.get("reviews") or [] if r.get("body")]
    return sorted(({**c, "body": redact(c["body"])} for c in rows if (c["at"] or "") > since), key=lambda c: c["at"])


def h_pr_view(a, cfg):
    """The ticket's PRs as JSON, with review comments newer than the ticket's reviewSeen."""
    t = state.load_ticket(cfg["id"], a.key)
    if not t["prs"]:
        raise Refused(f"no PRs recorded for {a.key}")
    out = []
    for p in t["prs"]:
        gh = _repo(cfg, p["repo"])["github"]
        v = json.loads(flowbase.GH(["pr", "view", str(p["number"]), "-R", gh, "--json",
                                    "state,mergeable,isDraft,headRefOid,statusCheckRollup,reviewDecision,comments,"
                                    "reviews"]))
        out.append({"repo": p["repo"], "number": p["number"], "url": p.get("url"),
                    **{k: v.get(k) for k in ("state", "mergeable", "isDraft", "headRefOid", "reviewDecision")},
                    "checks": _checks(v.get("statusCheckRollup") or []),
                    "newComments": _comments(gh, p["number"], v, t["reviewSeen"])})
    latest = max((c["at"] for p in out for c in p["newComments"]), default=t["reviewSeen"])
    _out({"prs": out, "reviewSeen": t["reviewSeen"], "latestCommentAt": latest})


def h_pr_log(a, cfg):
    """Failed-step log of a CI run on the ticket's branch in one of its PR repos: redacted, last 400 lines."""
    t = state.load_ticket(cfg["id"], a.key)
    for name in dict.fromkeys(p["repo"] for p in t["prs"]):
        gh = _repo(cfg, name)["github"]
        try:
            run = json.loads(flowbase.GH(["run", "view", str(a.run_id), "-R", gh, "--json", "headBranch"]))
        except (ProcError, ValueError):
            continue
        if t["branch"] and run.get("headBranch") == t["branch"]:
            log = flowbase.GH(["run", "view", str(a.run_id), "-R", gh, "--log-failed"])
            print("\n".join(redact(log).splitlines()[-400:]))
            return
    raise Refused(f"run {a.run_id} is not a run of {a.key}'s branch in its PR repos")


def h_pr_reply(a, cfg):
    gh = _repo(cfg, a.repo)["github"]
    if not any(p["repo"] == a.repo and p["number"] == a.number for p in state.load_ticket(cfg["id"], a.key)["prs"]):
        raise Refused(f"{a.repo}#{a.number} is not one of {a.key}'s PRs")
    body = redact(_crew_file(cfg, a.key, a.body_file))
    if _dry(cfg, a.key, f"reply on {a.repo}#{a.number}"):
        return
    print(flowbase.gh_with_body(["pr", "comment", str(a.number), "-R", gh], body).strip())


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


def h_runs_show(a, cfg):
    """Run lines for one ticket since its latest needs_you (all of them if it never asked)."""
    asked = [h["ts"] for h in state.load_ticket(cfg["id"], a.key)["history"] if h.get("stage") == "needs_you"]
    since = asked[-1] if asked else ""
    _out([r for r in state.recent_runs(cfg["id"], keep=100000) if r.get("key") == a.key and r.get("ts", "") >= since])


def h_build(a, cfg):
    try:
        from engine.lib import runner
    except ImportError:
        raise Refused("build runner not available") from None
    if a.sub == "start":
        if cfg["paused"]:
            raise Refused("paused")
        bad = [k for k in a.keys if not paths.TICKET_KEY_RE.fullmatch(k)]
        if bad:
            raise Usage(f"not a ticket key: {', '.join(bad)}")
        try:
            runner.start(cfg["id"], a.keys)
        except runner.RunnerBusy as e:
            raise Refused(str(e)) from None
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
        ("worktree", "prepare", h_wt_prepare, [k, (("repo",), {})]),
        ("worktree", "remove", h_wt_remove, [k]),
        ("ship", "", h_ship, [k, (("repo",), {}), (("--title",), {"required": True}),
                              (("--body-file",), {"required": True})]),
        ("merge", "", h_merge, [k, (("repo",), {}), (("pr_number",), {"type": int})]),
        ("push", "", h_push, [k, (("repo",), {})]),
        ("sync", "", h_sync, [k, (("repo",), {})]),
        ("pr", "view", h_pr_view, [k]),
        ("pr", "log", h_pr_log, [k, (("run_id",), {"type": int})]),
        ("pr", "reply", h_pr_reply, [k, (("repo",), {}), (("number",), {"type": int}),
                                     (("--body-file",), {"required": True})]),
        ("runs", "log", h_runs_log, [k, (("result",), {}), (("--note",), {"default": ""})]),
        ("runs", "show", h_runs_show, [k]),
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
    with contextlib.suppress(OSError, ValueError):
        state.relock_all(crew)  # a crash mid-window leaves a folder open; every sf call closes it again
    try:
        a.fn(a, config.load_crew(crew))
    except Usage as e:
        print(redact(str(e)), file=sys.stderr)
        return 2
    except (Refused, *_ERRORS) as e:
        print(redact(str(e)), file=sys.stderr)
        return 1
    return 0
