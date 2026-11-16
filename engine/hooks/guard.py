#!/usr/bin/env python3
"""PreToolUse hook: every crew tool call passes through decide(). Anything it can't check is blocked."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from engine.hooks.commands_allow import (
        ADD_LONG, ADD_SHORT, ALLOWED, ASSIGN, BRANCH_LIST_FLAGS, COMMIT_LONG, COMMIT_SHORT, CURL_OUT, ENV_DENY,
        ENV_OK, FILE_OPS, FIND_ACTIONS, GIT_CONFIG_OK, GIT_GLOBAL_FLAGS, GIT_LOCAL, GIT_LONG, GIT_READ,
        GIT_SHORT_DENY, HANDOFF, KEYWORDS, LANGS, MESSAGE_OPTS, NET_TOOLS, PATH_ARGS, READ_VALUES, SED_CMD,
        SED_FLAG, SYSTEM_BIN, TAG_LIST_FLAGS, TOOLCHAIN, WGET_OUT, WRAP_VALUES, WRAPPERS, WRITER_VALUES, Blocked,
        abbreviates, block, expand_long, interpreter, positionals, short_chars, short_value, toolchain)
    from engine.hooks.shell import SUB, Unparseable, lex, literal, structure
    from engine.lib import config, crewfs, paths
    _IMPORT_ERROR = None
except Exception as e:  # reported by main(), which then blocks
    _IMPORT_ERROR = e

FREE_TOOLS = {"TodoWrite", "Task", "Agent", "Skill", "WebSearch", "ExitPlanMode", "BashOutput",
              "KillShell", "KillBash"}
MEMORY_FILES = {"claude.md", "agents.md"}
BRANCH_PREFIX = "shopfloor/"
HOSTS = ("linear.app", "slack.com", "api.github.com")
NET_MSG = "tracker and chat writes go through sf"
ENV_FILES_OK = {".env.example", ".env.sample", ".env.template"}
BRACES = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
HOME_VARS = re.compile(r"\$(?:\{(HOME|TMPDIR)\}|(HOME|TMPDIR)\b)")


# ---- paths ----

def resolve(text: str, cwd: str | None) -> str | None:
    """Absolute real path of a word, or None when it depends on run-time values."""
    if SUB in text or BRACES.search(text):
        return None
    if text == "~" or text.startswith("~/"):
        text = os.path.expanduser("~") + text[1:]
    elif text.startswith("~"):
        return None  # ~user
    text = HOME_VARS.sub(lambda m: os.environ.get(m.group(1) or m.group(2)) or "$", text)
    if "$" in text:
        return None
    if not os.path.isabs(text):
        if cwd is None:
            return None
        text = os.path.join(cwd, text)
    return os.path.realpath(literal(text))


def _under(p: str, roots, fold: bool = False) -> bool:
    """p is one of roots or inside one; fold compares case-insensitively, as macOS file systems do."""
    if fold:
        p, roots = p.casefold(), [r.casefold() for r in roots]
    return any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)


def _env_name(base: str) -> bool:
    base = base.casefold()
    return (base == ".env" or base.startswith(".env.")) and base not in ENV_FILES_OK


def _read_candidates(w: str) -> list:
    """The paths a word could read: itself, a glued short-option value, an --opt= or k= value, minus @ or <."""
    out = [w]
    if w.startswith("-") and not w.startswith("--") and len(w) > 2:
        out.append(w[2:])
    if "=" in w:
        out.append(w.split("=", 1)[1])
    return out + [c[1:] for c in out if c[:1] in ("@", "<")]


def _glued_suffixes(w: str) -> list:
    """Every tail of a short-option cluster (-sT<path>, -sd@<path>): any letter may be the one taking a value."""
    if not w.startswith("-") or w.startswith("--"):
        return []
    tails = [w[i:] for i in range(2, len(w))]
    return tails + [t[1:] for t in tails if t[:1] in ("@", "<")]


def _dynamic(w: str) -> bool:
    return resolve(w, "/") is None


class Guard:
    def __init__(self, cfg: dict):
        flow = cfg.get("gitFlow") or {}
        repos = list((cfg.get("repos") or {}).values())
        tb = flow.get("testBranch")
        self.protected = set(cfg.get("protectedBranches") or []) | {r.get("base") for r in repos} | \
            ({tb} if tb else set())
        tracker = cfg.get("tracker") or {}
        self.move_targets = {m.split("->")[-1] for m in tracker.get("mayMove", [])}
        home = paths.shopfloor_home()
        self.secrets = [os.path.realpath(p) for p in config.secret_paths(cfg)]
        self.home = os.path.realpath(home)
        state = os.path.realpath(paths.crew_state_dir(cfg["id"]))
        self.tickets = os.path.join(state, "tickets")
        self.crew_dirs = [os.path.join(state, "worktrees"), self.tickets]
        roots = ["/tmp", "/private/tmp", os.environ.get("TMPDIR")]
        self.roots = self.crew_dirs + [os.path.realpath(r) for r in roots if r]
        self.devices = {os.path.realpath(d) for d in ("/dev/null", "/dev/stdout", "/dev/stderr")} | {"/dev/null"}
        self.repos = [os.path.realpath(os.path.expanduser(r["path"])) for r in repos if r.get("path")]
        # sf's records (ticket state, branch, briefing): sf and the board trust them, so no crew writes there
        self.no_write = [os.path.realpath(paths.ENGINE_DIR), os.path.join(state, "ticketstate")] + self.secrets + \
            self.repos
        self.sf_bin = os.path.realpath(paths.ENGINE_DIR / "bin" / "sf")
        self.branch_unknown = False

    # -- reads and writes --

    def touches_secret(self, p: str) -> bool:
        return _under(p, self.secrets, fold=True) or any(_under(s, [p], fold=True) for s in self.secrets)

    def secret(self, text: str, cwd: str | None, tree: bool = False) -> bool:
        if SUB in text:
            return False
        if any(c in text for c in "*?["):
            head = re.split(r"[*?\[]", text, maxsplit=1)[0]
            base = os.path.basename(text)
            if base.startswith(".") and base.casefold() not in ENV_FILES_OK and \
                    any(fnmatch.fnmatch(n, base.casefold()) for n in (".env", ".env.local", ".env.production")):
                return True
            p = resolve(head[:head.rfind("/") + 1] or ".", cwd)
            return p is not None and self.touches_secret(p)
        p = resolve(text, cwd)
        if p is None:
            return False
        if _env_name(os.path.basename(p)) or _env_name(os.path.basename(text.rstrip("/"))):
            return True
        return self.touches_secret(p) if tree else _under(p, self.secrets, fold=True)

    def read(self, text: str, cwd: str | None, tree: bool = False):
        if self.secret(text, cwd, tree):
            block(f"reading secret or off-limits files is not allowed ({literal(text)})")

    def write(self, text: str, cwd: str | None):
        p = resolve(text, cwd)
        if p is None:
            block(f"can't tell where {literal(text)!r} writes")
        if p in self.devices or p.startswith("/dev/fd/"):
            return
        parts = {x.casefold() for x in p.split("/") + literal(text).split("/")}
        if ".git" in parts or ".claude" in parts:
            block(f"{p} is inside .git or .claude, which crews don't write")
        if _under(p, self.no_write, fold=True) or \
                (_under(p, [self.home], fold=True) and not _under(p, self.crew_dirs)):
            block(f"{p} is off limits (engine, crew config, crew state, secrets, main checkouts)")
        base = os.path.basename(p).casefold()
        wts = self.crew_dirs[0]
        if base in MEMORY_FILES and _under(p, [wts], fold=True) and p[len(wts):].strip("/").count("/") <= 1:
            block(f"{p} would become instructions for every crew session; crews don't write it")
        if not _under(p, self.roots):
            block(f"writes stay in the crew's worktrees, tickets or a temp dir, not {p}")

    def code(self, text: str, cwd: str | None):
        """Programs, tests and configs a toolchain loads must come from the crew's worktrees."""
        p = resolve(text, cwd)
        if p is None or not _under(p, self.crew_dirs[:1]):
            block(f"programs, tests and configs run only from the crew's worktrees, not {literal(text)!r}")

    def search(self, tool: str, ti: dict, cwd: str):
        root = ti.get("path") or cwd
        pattern = ti.get("glob") if tool == "Grep" else ti.get("pattern")
        if not isinstance(root, str) or not (pattern is None or isinstance(pattern, str)):
            block(f"{tool} input is malformed")
        self.read(root, cwd, tree=True)
        if pattern:
            full = pattern if pattern.startswith(("/", "~")) else os.path.join(root, pattern)
            self.read(full, cwd, tree=True)

    # -- Bash --

    def check_text(self, text: str, cwd: str | None):
        self.script(lex(text, 0, None)[0], cwd)

    def script(self, toks: list, cwd: str | None):
        """Check each pipeline in every cwd it could run in: a cd only counts for sure along an unbroken &&
        chain from the start, so after ; || or & any cwd seen so far is possible."""
        stack, cwds, seen, exact = [], [cwd], [cwd], True
        for item in structure(toks):
            if item == "(":
                stack.append((cwds, seen, exact))
            elif item == ")":
                cwds, seen, exact = stack.pop()
            elif isinstance(item, str):
                if item != "&&" and exact:
                    exact, cwds = False, list(seen)
            else:
                new = list(dict.fromkeys(self.pipeline(item, c) for c in cwds))
                seen = list(dict.fromkeys(seen + new))
                cwds = new if exact else seen

    def pipeline(self, pipe: list, cwd: str | None):
        new_cwd = cwd
        for cmd in pipe:
            for sub in cmd.subs():
                self.script(sub, cwd)
            c = self.command(self.unwrap(cmd.words), cmd.redirs, cwd)
            if len(pipe) == 1:
                new_cwd = c
        return new_cwd

    def unwrap(self, words: list) -> list:
        words, names = list(words), []
        while words:
            w = words[0]
            m = ASSIGN.match(w)
            if m:
                names.append(m.group(1))
            if w in KEYWORDS or m:
                words.pop(0)
                continue
            if w not in WRAPPERS:
                break
            words.pop(0)
            if w == "command" and words[:1] in (["-v"], ["-V"]):
                return []
            while words and words[0].startswith("-") and len(words[0]) > 1:
                o = words.pop(0)
                if o == "--":
                    break
                if o in WRAP_VALUES.get(w, ()) and words:
                    words.pop(0)
            if w == "timeout" and words:
                words.pop(0)
        for n in names:
            if words and n not in ENV_OK:
                block(f"setting {n} for a command is not allowed")
            if ENV_DENY.fullmatch(n):
                block(f"setting {n} is not allowed")
        return words

    def executable(self, w0: str) -> str:
        if _dynamic(w0) or any(c in w0 for c in "*?["):
            block("can't check a command whose name is built at run time")
        if "/" not in w0:
            return w0
        if w0.startswith("/") and resolve(w0, "/") == self.sf_bin:
            return "sf"
        if w0 == "./gradlew" or os.path.dirname(w0) in SYSTEM_BIN:
            return os.path.basename(w0)
        block(f"run {os.path.basename(w0)} by name, not by path; scripts run through the toolchain (npm test, make)")

    def command(self, words: list, redirs: list, cwd: str | None):
        exe = self.executable(words[0]) if words else ""
        for op, target in redirs:
            if op in ("<<", "<<-", "<<<"):
                if LANGS.fullmatch(exe):
                    block("a heredoc or here-string fed to an interpreter can't be checked")
            elif op == "<":
                self.read(target, cwd)
            elif not (op in ("<&", ">&") and (target.isdigit() or target == "-")):
                self.write(target, cwd)
        if not words:
            return cwd
        if exe not in ALLOWED and not LANGS.fullmatch(exe):
            block(f"{exe} isn't on the crew's command list")
        if exe in ("cd", "pushd"):
            args = [a for a in words[1:] if a not in ("-L", "-P", "--")]
            return resolve(args[0] if args else "~", cwd) if args[:1] != ["-"] else None
        if exe == "popd":
            return None
        words = [words[0], *expand_long(exe, words[1:])]
        args = words[1:]
        for w in args:
            for c in _read_candidates(w):
                self.read(c, cwd, tree=exe != "ls")
            for c in _glued_suffixes(w):
                self.read(c, cwd)
        self.assignments(exe, args)
        if exe in PATH_ARGS and any(_dynamic(w) for w in args):
            block(f"{exe} with a run-time argument can't be checked")
        if exe == "env" and args:
            block("env may only print the environment")
        if exe == "export":
            for a in args:
                if a.partition("=")[0] not in ENV_OK:
                    block(f"export {a.partition('=')[0]} is not allowed")
        if exe == "set" and not all(re.fullmatch(r"[-+][euo]+|pipefail", a) for a in args):
            block("set may only turn on -e, -u or -o pipefail")
        if exe == "find" and any(a in FIND_ACTIONS for a in args):
            block("find may only list files (no -exec, -delete or -fprint)")
        if exe in TOOLCHAIN and not LANGS.fullmatch(exe):
            if any(os.path.basename(t) in HANDOFF for w in args for t in literal(w).split()):
                block(f"{exe} would run a command the guard can't follow; run it directly")
            toolchain(exe, words, lambda p: self.write(p, cwd), lambda p: self.code(p, cwd))
        if exe in ("sed", "awk", "gawk", "sort"):
            self.text_tool(exe, args)
        if exe == "deno" and args[:1] == ["eval"]:
            block("deno eval can't be checked")
        if exe == "git":
            self.git(words, cwd)
        elif exe == "gh":
            self.gh(words)
        elif exe == "sf":
            self.sf(words)
        elif exe in NET_TOOLS:
            self.network(exe, words, cwd)
        elif LANGS.fullmatch(exe):
            self.interp(exe, words, cwd)
        elif exe in FILE_OPS or exe in WRITER_VALUES:
            self.writes(exe, words, cwd)
        return cwd

    def assignments(self, exe: str, args: list):
        """Every way a command can set a shell variable: the dangerous names are never assigned."""
        names = []
        if exe == "for":
            names = args[:1]
        elif exe == "read":
            names = [a for k, a in enumerate(args) if not a.startswith("-") and not (k and args[k - 1] in READ_VALUES)]
        elif exe == "printf":
            names = [args[k + 1] for k, a in enumerate(args[:-1]) if a == "-v" or a.startswith("-v")]
            names += [a[2:] for a in args if a.startswith("-v") and len(a) > 2]
        for n in names:
            if ENV_DENY.fullmatch(literal(n)):
                block(f"setting {literal(n)} is not allowed")

    def text_tool(self, exe: str, args: list):
        for a in args:
            name = a.partition("=")[0]
            if exe != "sort" and exe != "sed" and (short_chars(a) & set("eilfE") or a.startswith("--") and abbreviates(
                    name, {"--source", "--include", "--file", "--load", "--exec"})):
                block(f"{exe} program files, includes and --source can't be checked")
            if exe == "sort" and a.startswith("--") and abbreviates(name, {"--compress-program"}):
                block("sort --compress-program runs a program the guard can't check")

    def interp(self, exe: str, words: list, cwd: str | None):
        args = words[1:]
        if exe == "bun" and [x for x in args if not x.startswith("-")][:1] == ["x"]:
            block("bun x runs another program the guard can't follow")
        if any(h in literal(" ".join(args)).lower() for h in HOSTS):
            block(NET_MSG)
        if exe == "ruby" and any(re.fullmatch(r"-[A-Za-z]*i.*", a) for a in args):
            block("ruby -i edits files the guard can't check; use the Edit tool")
        pos = [a for a in args if not a.startswith("-")]
        interpreter(exe, args, lambda p: self.write(p, cwd), lambda p: self.code(p, cwd))
        flags = {"--version", "--help", "-V", "-h"}
        if not pos and not any(a in flags or re.fullmatch(r"-[A-Za-z]*m.*", a) for a in args):
            block(f"{exe} reading its program from stdin can't be checked")

    def network(self, exe: str, words: list, cwd: str | None):
        if any(h in literal(" ".join(words)).lower() for h in HOSTS):
            block(NET_MSG)
        if any(SUB in w or "$" in w for w in words[1:]):
            block(f"{exe} with run-time arguments can't be checked")
        curl = exe == "curl"
        config_opts = ("--config",) if curl else ("--input-file", "--execute", "--config")
        config_chars = "K" if curl else "ie"
        out_opts = CURL_OUT if curl else WGET_OUT
        to_cwd = not curl and exe == "wget"
        for k, a in enumerate(words[1:], 1):
            name, eq, val = a.partition("=")
            if name in config_opts or any(short_value(words, k, c) is not None for c in config_chars):
                block(f"{exe} {name} reads options the guard can't check")
            target = val if eq and name in out_opts else words[k + 1] if a in out_opts and k + 1 < len(words) \
                else short_value(words, k, "o" if curl else "O")
            if target is not None and target != "-":
                self.write(target, cwd)
                to_cwd = False
            if curl and (a in ("-O", "--remote-name", "--remote-name-all") or
                         (not a.startswith("--") and a.startswith("-") and "O" in a)):
                to_cwd = True
        if to_cwd:
            self.write(".", cwd)

    def writes(self, exe: str, words: list, cwd: str | None):
        pos, opts = positionals(words, WRITER_VALUES.get(exe, set()))
        targets = pos
        if exe in ("cp", "mv"):
            if exe == "cp" and any(short_chars(a) & set("sl") or a in ("--link", "--symbolic-link") for a in words):
                block("cp -s/-l makes links the guard can't follow")
            t = opts.get("-t") or opts.get("--target-directory")
            if exe == "mv":
                targets = pos + ([t] if t else [])
            else:
                targets = [t] if t else pos[-1:] if len(pos) > 1 else []
        elif exe == "chmod":
            targets = pos if any(w.startswith("--reference") for w in words) else pos[1:]
        elif exe in ("sed", "awk", "gawk"):
            targets = self.program(exe, words, pos, opts)
        elif exe == "sort":
            targets = [v for k, v in opts.items() if k in ("-o", "--output")]
        elif exe == "uniq":
            targets = pos[1:2]
        for t in targets:
            self.write(t, cwd)

    def program(self, exe: str, words: list, pos: list, opts: dict) -> list:
        if "-f" in opts or "--file" in opts:
            block(f"{exe} -f runs a program file the guard can't check")
        progs = [words[k + 1] for k, a in enumerate(words[:-1]) if a in ("-e", "--expression")] + \
            [a.partition("=")[2] for a in words if a.startswith("--expression=")]
        files = pos if progs else pos[1:]
        for prog in map(literal, progs + pos):  # file names are checked too: sed -i '' puts the program there
            if exe != "sed" and any(t in prog for t in (">", "|", "system", "getline", "close(")):
                block("awk programs that write, pipe or run commands can't be checked")
            if exe == "sed" and (SED_CMD.search(prog) or SED_FLAG.search(prog)):
                block("sed w, r and e commands can't be checked")
        in_place = exe == "sed" and any(short_chars(w) & {"i", "I"} or w.startswith("--in-place") for w in words[1:])
        return files if in_place else []

    # -- git, gh, sf --

    def branch(self, cwd: str | None) -> str | None:
        """The branch checked out in the crew clone holding cwd, read from .git/HEAD as text: the hook runs
        outside the sandbox, so it never runs git where a crew can write."""
        d = cwd
        while d and not self.branch_unknown and _under(d, self.crew_dirs[:1]):
            g = os.path.join(d, ".git")
            wts = self.crew_dirs[0]
            try:
                found = crewfs.exists(wts, os.path.relpath(g, wts))
            except OSError:
                return None
            if found:
                # HEAD is detached mid-rebase; git keeps the branch the rebase will update in head-name
                for name in ("HEAD", "rebase-merge/head-name", "rebase-apply/head-name"):
                    try:
                        head = crewfs.read_bytes(wts, os.path.relpath(os.path.join(g, name), wts), 512).decode().strip()
                    except (OSError, ValueError):
                        continue
                    head = head.removeprefix("ref: ")
                    if head.startswith("refs/heads/") and head[len("refs/heads/"):]:
                        return head[len("refs/heads/"):]
                return None
            d = os.path.dirname(d)
        return None

    def current(self, cwd: str | None, what: str) -> str:
        return self.branch(cwd) or block(f"can't tell the current branch for this {what}")

    def git(self, words: list, cwd: str | None):
        i = 1
        while i < len(words) and words[i].startswith("-"):
            w = words[i]
            if w in GIT_GLOBAL_FLAGS:
                i += 1
                continue
            if w not in ("-C", "-c") or i + 1 >= len(words):
                block(f"git option {w.partition('=')[0]} is not allowed")
            v, i = words[i + 1], i + 2
            if w == "-c":
                self.git_key(v.split("=", 1)[0], write=True)
                continue
            cwd = resolve(v, cwd)
            if cwd is None or not _under(cwd, self.roots) or _under(cwd, self.repos, fold=True):
                block("git -C may only point into the crew's worktrees")
        sub, rest = (words[i], words[i + 1:]) if i < len(words) else ("", [])
        if sub in ("push", "pull") or sub == "fetch" and not (rest and self.ticket_bundle(rest[0], cwd)):
            block(f"crews don't run git {sub}; sf push and sf sync do it outside the sandbox, through bundles in "
                  f"the ticket folder")
        if sub not in GIT_READ | GIT_LOCAL:
            block(f"git {literal(sub).replace(SUB, '$(...)')} is not on the guard's list of git commands")
        if sub not in GIT_READ and (cwd is None or _under(cwd, self.repos)):
            block(f"git {sub} in the main checkout is not allowed; work in the ticket worktree")
        for k, a in enumerate(rest):
            if a == "--":
                break
            if k and rest[k - 1] in MESSAGE_OPTS:
                continue
            if _dynamic(a) and not a.startswith(("-m", "--message=")):
                block(f"git {sub} with a run-time argument can't be checked")
            if a.startswith("--") and a.partition("=")[0] not in GIT_LONG.get(sub, set()):
                block(f"git {sub} {a.partition('=')[0]} is not on the guard's list of options")
            if short_chars(a) & GIT_SHORT_DENY.get(sub, set()) and not a.startswith(("-m", "-F")):
                block(f"git {sub} {a} opens an editor or prompt the guard can't check")
        getattr(self, "git_" + sub.replace("-", "_"), lambda rest, cwd: None)(rest, cwd)

    def ticket_bundle(self, word: str, cwd: str | None) -> bool:
        p = resolve(word, cwd)
        return p is not None and p.casefold().endswith(".bundle") and _under(p, [self.tickets]) and p != self.tickets

    def git_fetch(self, rest, cwd):
        """Only from a bundle file in the ticket folder: sf sync writes one; the network stays out of reach."""
        if any(a.startswith("-") for a in rest):
            block("git fetch from a bundle takes no options")

    def git_bundle(self, rest, cwd):
        """Only git bundle create <ticket folder>/<name>.bundle <branch>: how a crew hands its branch to sf push."""
        if rest[:1] != ["create"] or len(rest) < 3 or not self.ticket_bundle(rest[1], cwd) or \
                any(a.startswith("-") for a in rest[1:]):
            block("git bundle may only create a .bundle file in the ticket folder from named branches")
        self.write(rest[1], cwd)

    def git_options(self, sub: str, rest: list, short: set, long: set):
        """Only the listed options: anything else could open an editor or a prompt."""
        for k, a in enumerate(rest):
            if k and rest[k - 1] in MESSAGE_OPTS:
                continue
            if a.startswith("--") and a != "--" and a.partition("=")[0] not in long:
                block(f"git {sub} {a.partition('=')[0]} is not allowed")
            chars = short_chars(a)
            if a.startswith("-m") or a.startswith("-F"):
                chars = set()  # -mmessage / -Ffile carry their value
            if chars - short:
                block(f"git {sub} -{''.join(sorted(chars - short))} is not allowed; use -m for the message")

    def git_commit(self, rest, cwd):
        self.git_options("commit", rest, COMMIT_SHORT, COMMIT_LONG)

    def git_add(self, rest, cwd):
        self.git_options("add", rest, ADD_SHORT, ADD_LONG)

    def git_checkout(self, rest, cwd):
        self.branch_unknown = True

    git_switch = git_checkout

    def git_merge(self, rest, cwd):
        if not any(a in ("--abort", "--quit", "--continue") for a in rest):
            b = self.current(cwd, "merge")
            if b in self.protected:
                block(f"merging into {b} is not allowed; sf merge does that when the flow allows it")

    def git_rebase(self, rest, cwd):
        if any(a.startswith(("--exec", "--interactive")) or short_chars(a) & {"x", "i"} for a in rest):
            block("git rebase -i/--exec runs an editor or commands the guard can't check")
        if self.current(cwd, "rebase") in self.protected:
            block("rebasing a protected branch is not allowed")

    def git_reset(self, rest, cwd):
        if "--hard" in rest and self.current(cwd, "reset") in self.protected:
            block("git reset --hard on a protected branch is not allowed")
        if any(a.startswith("--patch") or "p" in short_chars(a) for a in rest):
            block("git reset -p is interactive")

    def git_grep(self, rest, cwd):
        if any(a == "-O" or a.startswith(("-O", "--open-files-in-pager")) for a in rest):
            block("git grep -O runs a pager command the guard can't check")

    def git_branch(self, rest, cwd):
        bad = [a for a in rest if a.startswith("-") and a.partition("=")[0] not in BRANCH_LIST_FLAGS]
        if bad:
            block(f"git branch {bad[0]} is not allowed; list or create branches only")

    def git_tag(self, rest, cwd):
        listing = not rest or any(a in ("-l", "--list") for a in rest)
        if not listing or any(a.startswith("-") and a.partition("=")[0] not in TAG_LIST_FLAGS for a in rest):
            block("git tag may only list tags")

    def git_remote(self, rest, cwd):
        if rest[:1] not in ([], ["-v"], ["--verbose"], ["show"], ["get-url"]):
            block("git remote may only show remotes")

    def git_worktree(self, rest, cwd):
        if rest[:1] != ["list"]:
            block("git worktree may only list worktrees; sf manages them")

    def git_config(self, rest, cwd):
        reads = any(a.startswith("--get") or a in ("-l", "--list") for a in rest)
        pos = [a for a in rest if not a.startswith("-")]
        flags = [a for a in rest if a.startswith("-") and not (a.startswith("--get") or a in ("-l", "--list"))]
        if reads and not flags or pos[:1] in (["get"], ["list"]) and not flags or (len(pos) == 1 and not rest[1:]):
            return
        block("git config may only read settings")

    def git_key(self, key: str, write: bool):
        if write and key.lower() not in GIT_CONFIG_OK:
            block(f"git config {key} is not allowed; only user.name and user.email may be set")

    def gh(self, words: list):
        block("crews don't run gh; use sf pr view, sf pr log, sf pr reply, sf ship or sf merge")

    def sf(self, words: list):
        # every sf option takes a value, so the word after one is never a positional
        pos = [w for k, w in enumerate(words[1:], 1)
               if not w.startswith("-") and not (words[k - 1].startswith("--") and "=" not in words[k - 1])]
        if any(_dynamic(p) for p in pos):
            block("can't check an sf command built at run time")
        a, b = (pos + ["", ""])[:2]
        # in dry run sf itself refuses writes and logs the "would" line, so the crew sees what it would have done
        if (a, b) == ("tracker", "transition"):
            to = pos[3] if len(pos) > 3 else ""
            if _dynamic(to) or to not in self.move_targets:
                block(f"tracker.mayMove doesn't allow moving a ticket to {to!r}")


def _field(ti: dict, key: str) -> str:
    v = ti.get(key)
    if not isinstance(v, str) or not v:
        block(f"tool input has no {key}")
    return v


def decide(tool: str, tool_input: dict, cwd: str, cfg: dict) -> str | None:
    """The reason to block this tool call, or None to allow it."""
    cwd = cwd or os.getcwd()
    try:
        if not isinstance(tool_input, dict):
            block("tool input is not an object")
        if tool in FREE_TOOLS:
            return None
        g = Guard(cfg)
        if tool == "Bash":
            g.check_text(_field(tool_input, "command"), cwd)
        elif tool == "Read":
            g.read(_field(tool_input, "file_path"), cwd)
        elif tool in ("Edit", "MultiEdit", "Write"):
            g.write(_field(tool_input, "file_path"), cwd)
        elif tool == "NotebookEdit":
            g.write(_field(tool_input, "notebook_path"), cwd)
        elif tool in ("Grep", "Glob"):
            g.search(tool, tool_input, cwd)
        else:
            block(f"tool not allowed for crews: {tool}")
    except Blocked as e:
        return str(e)
    except (Unparseable, RecursionError, IndexError):
        return "can't check this command"
    return None


def _run(raw: str) -> str | None:
    if _IMPORT_ERROR is not None:
        return f"can't load the engine: {_IMPORT_ERROR}"
    crew = os.environ.get("SHOPFLOOR_CREW")
    if not crew:
        return "SHOPFLOOR_CREW is not set"
    try:
        event = json.loads(raw)
    except ValueError:
        return "hook input is not valid JSON"
    if not (isinstance(event, dict) and isinstance(event.get("tool_name"), str)
            and isinstance(event.get("tool_input"), dict)):
        return "hook input is malformed"
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
    return decide(event["tool_name"], event["tool_input"], cwd, config.load_crew(crew))


def main() -> int:
    try:
        reason = _run(sys.stdin.read())
    except Exception as e:
        reason = str(e) or type(e).__name__
    if reason:
        print(f"guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
