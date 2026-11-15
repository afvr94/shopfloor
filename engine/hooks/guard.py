#!/usr/bin/env python3
"""PreToolUse hook: every crew tool call passes through decide(). Anything it can't check is blocked."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from engine.hooks.shell import SUB, Unparseable, lex, literal, structure
    from engine.lib import config, paths
    from engine.lib.cli import WRITE_COMMANDS
    _IMPORT_ERROR = None
except Exception as e:  # reported by main(), which then blocks
    _IMPORT_ERROR = e

FREE_TOOLS = {"TodoWrite", "Task", "Agent", "Skill", "WebSearch", "WebFetch", "ExitPlanMode", "BashOutput",
              "KillShell", "KillBash"}
BRANCH_PREFIX = "shopfloor/"
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
LANGS = re.compile(r"(python[\d.]*|node|nodejs|perl|ruby|deno|bun|php)")
NET_TOOLS = {"curl", "wget", "http", "https", "httpie", "xh", "nc", "ncat", "netcat"}
HOSTS = ("linear.app", "slack.com", "api.github.com")
NET_MSG = "tracker and chat writes go through sf"
# shortcut: a text tripwire for inline code; a script file that pushes is not seen
INLINE_GIT = re.compile(r"\bgit\W+(?:\S+\W+)?(?:push|merge)\b|\bgh\W+(?:pr|api)\b")
WRAPPERS = {"sudo", "command", "builtin", "time", "nohup", "nice", "exec", "timeout", "env", "xargs",
            "stdbuf", "caffeinate"}
WRAP_VALUES = {"sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"}, "nice": {"-n"},
               "timeout": {"-s", "-k", "--signal", "--kill-after"}, "env": {"-u", "--unset", "-C", "--chdir", "-P"},
               "xargs": {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a"}, "stdbuf": {"-i", "-o", "-e"},
               "caffeinate": {"-t", "-w"}, "exec": {"-a"}}
KEYWORDS = {"!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}"}
NEVER = {"eval", "alias", "trap", "case", "select", "coproc", "function"}
DECLARE = {"export", "declare", "typeset", "readonly", "local"}
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
TOOL_ENV = re.compile(r"(?:GIT|GH)_")
WRITERS = {"rm", "rmdir", "touch", "mkdir", "truncate", "unlink", "shred", "mv", "tee", "cp", "install", "ln",
           "chmod", "chown", "chgrp", "dd", "sed", "awk", "sort", "uniq", "rsync", "tar", "unzip", "patch"}
WRITER_VALUES = {"cp": {"-t", "--target-directory", "-S", "--suffix"}, "mv": {"-t", "--target-directory", "-S", "--suffix"},
                 "ln": {"-t", "--target-directory", "-S", "--suffix"},
                 "install": {"-t", "--target-directory", "-S", "--suffix", "-m", "-o", "-g", "--mode", "--owner", "--group"},
                 "mkdir": {"-m", "--mode"}, "touch": {"-r", "-t", "-d", "--reference", "--date"},
                 "truncate": {"-s", "-r", "--size", "--reference"}, "shred": {"-n", "-s", "--iterations", "--size"},
                 "sed": {"-e", "-f", "--expression", "--file"}, "awk": {"-f", "-v", "-F", "--file"},
                 "sort": {"-o", "--output", "-k", "-t", "-S", "-T"}, "unzip": {"-d"}, "patch": {"-d", "-o", "-p", "-i"}}
READERS = {"cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "cp", "source", ".", "base64",
           "xxd", "strings", "od", "hexdump", "sed", "awk", "nl", "tac", "sort", "uniq", "diff", "cut", "jq"}
SAFE = {"echo", "printf", "ls", "which", "type", "whereis", "man", "wc", "tr", "test", "[", "[[", "true", "false",
        "basename", "dirname", "realpath", "readlink", "stat", "file", "du", "df", "date", "pwd", "sleep", "cd",
        "pushd", "popd", "unset", "read", "set", "shift", "return", "exit", "for", "find", "base64"} \
    | READERS | WRITERS | DECLARE | NET_TOOLS | {"git", "gh", "sf"}
XARGS_UNSAFE = {"git", "gh", "sf", "find", "eval"} | NET_TOOLS | SHELLS | WRITERS | READERS
ENV_OK = {".env.example", ".env.sample", ".env.template"}
GH_READ = {("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"), ("pr", "status"), ("run", "view"),
           ("run", "list"), ("run", "watch"), ("repo", "view"), ("issue", "view"), ("issue", "list"),
           ("auth", "status"), ("workflow", "list"), ("workflow", "view"), ("release", "list"), ("release", "view")}
GIT_READ = {"", "status", "diff", "log", "show", "rev-parse", "ls-files", "grep", "blame", "merge-base", "describe",
            "fetch", "shortlog", "show-ref", "rev-list", "branch", "tag", "remote", "config", "worktree"}
GIT_LOCAL = {"add", "commit", "checkout", "switch", "restore", "reset", "stash", "rebase", "merge", "push",
             "cherry-pick", "mv", "rm"}
GIT_GLOBAL_FLAGS = {"--no-pager", "-P", "--no-optional-locks", "--version", "--help", "--literal-pathspecs"}
GIT_CONFIG_OK = {"user.name", "user.email"}
BRANCH_LIST_FLAGS = {"-a", "--all", "-r", "--remotes", "-l", "--list", "-v", "-vv", "--verbose", "--show-current",
                     "--contains", "--no-contains", "--merged", "--no-merged", "--sort", "--format", "-q", "--quiet",
                     "--color", "--no-color", "--points-at", "--column", "--no-column"}
TAG_LIST_FLAGS = {"-l", "--list", "-n", "--contains", "--no-contains", "--points-at", "--sort", "--merged",
                  "--no-merged", "--format", "--column"}
SED_CMD = re.compile(r"(?:^|[;{}\n])\s*(?:[0-9$]+|/(?:\\.|[^/])*/)?(?:,(?:[0-9$]+|/(?:\\.|[^/])*/))?\s*!?\s*[wWer]\b")
SED_FLAG = re.compile(r"s(.)(?:\\.|(?!\1).)*\1(?:\\.|(?!\1).)*\1[gpIiMm0-9]*[we]")
BRACES = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
HOME_VARS = re.compile(r"\$(?:\{(HOME|TMPDIR)\}|(HOME|TMPDIR)\b)")


class Blocked(Exception):
    pass


def block(why: str):
    raise Blocked(why)


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


def _under(p: str, roots) -> bool:
    return any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)


def _env_name(base: str) -> bool:
    return (base == ".env" or base.startswith(".env.")) and base not in ENV_OK


def _dynamic(w: str) -> bool:
    return resolve(w, "/") is None


def _interp(exe: str) -> bool:
    return exe in SHELLS or bool(LANGS.fullmatch(exe))


def _runner(w: str) -> bool:
    name = os.path.basename(w)
    return name in {"git", "gh", "sf"} | NET_TOOLS or _interp(name)


def _positionals(words: list, value_opts: set):
    pos, opts, k, end = [], {}, 1, False
    while k < len(words):
        a = words[k]
        k += 1
        if end or not a.startswith("-") or a == "-":
            pos.append(a)
        elif a == "--":
            end = True
        elif a.split("=", 1)[0] in value_opts:
            if "=" in a:
                opts[a.split("=", 1)[0]] = a.split("=", 1)[1]
            elif k < len(words):
                opts[a] = words[k]
                k += 1
        elif not a.startswith("--") and a[:2] in value_opts:
            opts[a[:2]] = a[2:]
    return pos, opts


def _short_value(words: list, k: int, ch: str) -> str | None:
    """The value of short option ch inside the cluster words[k] (-sSo file, -ofile), or None."""
    a = words[k]
    if a.startswith("--") or not a.startswith("-") or ch not in a[1:]:
        return None
    rest = a[a.index(ch, 1) + 1:]
    return rest or (words[k + 1] if k + 1 < len(words) else "")


class Guard:
    def __init__(self, cfg: dict):
        self.dry = cfg.get("dryRun", True) is not False
        flow = cfg.get("gitFlow") or {}
        repos = list((cfg.get("repos") or {}).values())
        tb = flow.get("testBranch")
        self.protected = set(cfg.get("protectedBranches") or []) | {r.get("base") for r in repos} | \
            ({tb} if tb else set())
        tracker, g = cfg.get("tracker") or {}, cfg.get("guard") or {}
        self.move_targets = {m.split("->")[-1] for m in tracker.get("mayMove", [])}
        home = paths.shopfloor_home()
        tokens = [tracker.get("tokenFile"), (cfg.get("chat") or {}).get("tokenFile")]
        secret = [home / "secrets", *g.get("secretFiles", []), *g.get("offLimits", []), *tokens]
        self.secrets = [os.path.realpath(os.path.expanduser(str(p))) for p in secret if p]
        self.home = os.path.realpath(home)
        state = os.path.realpath(paths.crew_state_dir(cfg["id"]))
        self.tickets = os.path.join(state, "tickets")
        self.crew_dirs = [os.path.join(state, "worktrees"), self.tickets]
        roots = ["/tmp", "/private/tmp", os.environ.get("TMPDIR")]
        self.roots = self.crew_dirs + [os.path.realpath(r) for r in roots if r]
        self.devices = {os.path.realpath(d) for d in ("/dev/null", "/dev/stdout", "/dev/stderr")} | {"/dev/null"}
        self.repos = [os.path.realpath(os.path.expanduser(r["path"])) for r in repos if r.get("path")]
        self.no_write = [os.path.realpath(paths.ENGINE_DIR)] + self.secrets + self.repos
        self.inline_secrets = [self.home, "secrets", ".env"] + [os.path.expanduser(t) for t in tokens if t]
        self.branch_unknown = False

    # -- reads and writes --

    def touches_secret(self, p: str) -> bool:
        return _under(p, self.secrets) or any(_under(s, [p]) for s in self.secrets)

    def secret(self, text: str, cwd: str | None, tree: bool = False) -> bool:
        if SUB in text:
            return False
        if any(c in text for c in "*?["):
            head = re.split(r"[*?\[]", text, maxsplit=1)[0]
            base = os.path.basename(text)
            if base.startswith(".") and base not in ENV_OK and \
                    any(fnmatch.fnmatchcase(n, base) for n in (".env", ".env.local", ".env.production")):
                return True
            p = resolve(head[:head.rfind("/") + 1] or ".", cwd)
            return p is not None and self.touches_secret(p)
        p = resolve(text, cwd)
        if p is None:
            return False
        if _env_name(os.path.basename(p)) or _env_name(os.path.basename(text.rstrip("/"))):
            return True
        return self.touches_secret(p) if tree else _under(p, self.secrets)

    def read(self, text: str, cwd: str | None, tree: bool = False):
        if self.secret(text, cwd, tree):
            block(f"reading secret or off-limits files is not allowed ({literal(text)})")

    def write(self, text: str, cwd: str | None):
        p = resolve(text, cwd)
        if p is None:
            block(f"can't tell where {literal(text)!r} writes")
        if p in self.devices or p.startswith("/dev/fd/"):
            return
        parts = set(p.split("/")) | set(literal(text).split("/"))
        if ".git" in parts or ".claude" in parts:
            block(f"{p} is inside .git or .claude, which crews don't write")
        if _under(p, self.no_write) or (_under(p, [self.home]) and not _under(p, self.crew_dirs)):
            block(f"{p} is off limits (engine, crew config, crew state, secrets, main checkouts)")
        if _under(p, [self.tickets]) and (os.path.basename(p) == "state.json" or p.endswith(".lock")):
            block(f"{p} belongs to sf")
        if not _under(p, self.roots):
            block(f"writes stay in the crew's worktrees, tickets or a temp dir, not {p}")

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
        stack = []
        for item in structure(toks):
            if item == "(":
                stack.append(cwd)
            elif item == ")":
                cwd = stack.pop()
            else:
                cwd = self.pipeline(item, cwd)

    def pipeline(self, pipe: list, cwd: str | None):
        new_cwd, decoded = cwd, False
        for cmd in pipe:
            for sub in cmd.subs():
                self.script(sub, cwd)
            words, here, flags = self.unwrap(cmd.words, cwd)
            exe = os.path.basename(words[0]) if words else ""
            if exe == "base64" and any(a in ("-d", "-D", "--decode") for a in words[1:]):
                decoded = True
            elif decoded and _interp(exe):
                block("decoded text piped into an interpreter can't be checked")
            c = self.command(words, cmd.redirs, here, flags)
            if len(pipe) == 1:
                new_cwd = c
        return new_cwd

    def unwrap(self, words: list, cwd: str | None):
        words, flags = list(words), set()
        while words:
            w = words[0]
            if ASSIGN.match(w) and TOOL_ENV.match(w):
                block("setting GIT_* or GH_* variables is not allowed")
            if w in KEYWORDS or ASSIGN.match(w):
                words.pop(0)
                continue
            name = os.path.basename(w)
            if name not in WRAPPERS:
                break
            words.pop(0)
            flags.add(name)
            if name == "command" and words[:1] in (["-v"], ["-V"]):
                return [], cwd, flags
            while words and words[0].startswith("-") and len(words[0]) > 1:
                o = words.pop(0)
                if o == "--":
                    break
                if name == "env" and (o.startswith("-S") or o.startswith("--split-string")):
                    block("env -S can't be checked")
                if name == "env" and o.startswith("--chdir="):
                    cwd = resolve(o.split("=", 1)[1], cwd)
                elif o in WRAP_VALUES.get(name, ()) and words:
                    v = words.pop(0)
                    if name == "env" and o in ("-C", "--chdir"):
                        cwd = resolve(v, cwd)
            if name == "timeout" and words:
                words.pop(0)
        return words, cwd, flags

    def command(self, words: list, redirs: list, cwd: str | None, flags: set):
        exe = os.path.basename(words[0]) if words else ""
        for op, target in redirs:
            if op in ("<<", "<<-", "<<<"):
                if _interp(exe):
                    block("a heredoc or here-string fed to an interpreter can't be checked")
            elif op == "<":
                self.read(target, cwd)
            elif not (op in ("<&", ">&") and (target.isdigit() or target == "-")):
                self.write(target, cwd)
        if not words:
            return cwd
        if _dynamic(words[0]) or any(c in words[0] for c in "*?["):
            block("can't check a command whose name is built at run time")
        if exe in ("cd", "pushd"):
            args = [a for a in words[1:] if a not in ("-L", "-P", "--")]
            return resolve(args[0] if args else "~", cwd) if args[:1] != ["-"] else None
        if exe == "popd":
            return None
        for w in words[1:]:
            for c in [w] + ([w[1:]] if w.startswith("@") else []) + ([w.split("=", 1)[1]] if "=" in w else []):
                self.read(c, cwd, tree=exe != "ls")
        if exe in NEVER:
            block(f"{exe} can't be checked; run the command directly")
        if exe in DECLARE and any(TOOL_ENV.match(w) for w in words[1:]):
            block("setting GIT_* or GH_* variables is not allowed")
        if exe not in SAFE and not _interp(exe) and any(_runner(w) for w in words[1:]):
            block(f"{exe} runs another command the guard can't follow; run it directly")
        if exe in ("git", "gh", "sf") and any(BRACES.search(w) for w in words):
            block(f"brace expansion in {exe} arguments can't be checked")
        if "xargs" in flags and (exe in XARGS_UNSAFE or _interp(exe)):
            block(f"xargs {exe} gets its arguments at run time, so it can't be checked")
        if "exec" in flags and exe in SHELLS:
            block("exec of a shell can't be checked")
        if exe in ("source", ".") and (len(words) < 2 or any(_dynamic(w) for w in words[1:])):
            block("source of run-time input can't be checked")
        if exe in READERS and any(_dynamic(w) for w in words[1:]):
            block(f"{exe} of a run-time path can't be checked")
        if exe == "git":
            self.git(words, cwd)
        elif exe == "gh":
            self.gh(words)
        elif exe == "sf":
            self.sf(words)
        elif exe in NET_TOOLS:
            self.network(exe, words, cwd)
        elif _interp(exe):
            self.interp(exe, words, cwd)
        elif exe in WRITERS:
            self.writes(exe, words, cwd)
        elif exe == "find":
            self.find(words, cwd)
        return cwd

    def interp(self, exe: str, words: list, cwd: str | None):
        args = words[1:]
        text = literal(" ".join(args))
        if any(h in text.lower() for h in HOSTS):
            block(NET_MSG)
        if exe not in SHELLS:
            if INLINE_GIT.search(text):
                block("inline code that runs git push/merge or gh can't be checked; run the command directly")
            if any(s in text for s in self.inline_secrets):
                block("interpreter code that names secrets, .env or the shopfloor home is not allowed")
            if exe in ("perl", "ruby") and any(re.fullmatch(r"-[A-Za-z]*i.*", a) for a in args):
                block(f"{exe} -i edits files the guard can't check; use the Edit tool")
        if exe in SHELLS:
            for k, a in enumerate(args):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a):
                    code = literal(args[k + 1]) if k + 1 < len(args) else block("shell -c without a command")
                    if SUB in code or "$(" in code or "`" in code:
                        block("shell -c with a substitution can't be checked")
                    return self.check_text(code, cwd)
            script = next((a for a in args if not a.startswith("-") or a == "-"), None)
            if script in (None, "-") or "-s" in args:
                block("a shell reading its program from stdin can't be checked")
            return
        pos = [a for a in args if not a.startswith("-")]
        if pos and os.path.basename(pos[0]) == "sf":
            return self.sf(words[words.index(pos[0]):])
        flags = {"--eval", "--print", "--version", "--help", "-V", "-h"}
        if not pos and not any(a in flags or re.fullmatch(r"-[A-Za-z]*[ceEmp]", a) for a in args):
            block(f"{exe} reading its program from stdin can't be checked")

    def network(self, exe: str, words: list, cwd: str | None):
        if any(h in literal(" ".join(words)).lower() for h in HOSTS):
            block(NET_MSG)
        if any(SUB in w or "$" in w for w in words[1:]):
            block(f"{exe} with run-time arguments can't be checked")
        curl = exe == "curl"
        config_opts = ("--config",) if curl else ("--input-file", "--execute", "--config")
        config_chars = "K" if curl else "ie"
        out_opts = {"-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", "--trace", "--trace-ascii",
                    "--stderr", "--output-dir"} if curl else {"-O", "--output-document", "-o", "--output-file",
                                                               "-a", "--append-output", "-P", "--directory-prefix"}
        to_cwd = not curl and exe == "wget"
        for k, a in enumerate(words[1:], 1):
            name, eq, val = a.partition("=")
            if name in config_opts or any(_short_value(words, k, c) is not None for c in config_chars):
                block(f"{exe} {name} reads options the guard can't check")
            target = val if eq and name in out_opts else words[k + 1] if a in out_opts and k + 1 < len(words) \
                else _short_value(words, k, "o" if curl else "O")
            if target is not None and target != "-":
                self.write(target, cwd)
                to_cwd = False
            if curl and (a in ("-O", "--remote-name", "--remote-name-all") or
                         (not a.startswith("--") and a.startswith("-") and "O" in a)):
                to_cwd = True
        if to_cwd:
            self.write(".", cwd)

    def writes(self, exe: str, words: list, cwd: str | None):
        pos, opts = _positionals(words, WRITER_VALUES.get(exe, set()))
        targets = pos
        if exe in ("cp", "install", "ln", "mv"):
            t = opts.get("-t") or opts.get("--target-directory")
            if exe == "mv":
                targets = pos + ([t] if t else [])
            else:
                targets = [t] if t else pos[-1:] if len(pos) > 1 else [os.path.basename(p) for p in pos[:1]]
        elif exe in ("chmod", "chown", "chgrp"):
            targets = pos if any(w.startswith("--reference") for w in words) else pos[1:]
        elif exe == "dd":
            targets = [w[3:] for w in words[1:] if w.startswith("of=")]
        elif exe in ("sed", "awk"):
            targets = self.program(exe, words, pos, opts)
        elif exe == "sort":
            targets = [v for k, v in opts.items() if k in ("-o", "--output")]
        elif exe == "uniq":
            targets = pos[1:2]
        elif exe == "rsync":
            targets = pos[-1:]
        elif exe == "tar":
            targets = self.tar(words)
        elif exe == "unzip":
            targets = [] if any(a in ("-l", "-p", "-t", "-Z", "-v") for a in words) else [opts.get("-d", ".")]
        elif exe == "patch":
            targets = [opts.get("-d", ".")] + ([opts["-o"]] if "-o" in opts else [])
        for t in targets:
            self.write(t, cwd)

    def program(self, exe: str, words: list, pos: list, opts: dict) -> list:
        if "-f" in opts or "--file" in opts:
            block(f"{exe} -f runs a program file the guard can't check")
        progs = [words[k + 1] for k, a in enumerate(words[:-1]) if a in ("-e", "--expression")] + \
            [a.partition("=")[2] for a in words if a.startswith("--expression=")]
        files = pos if progs else pos[1:]
        for prog in map(literal, progs + pos):  # file names are checked too: sed -i '' puts the program there
            if exe == "awk" and any(t in prog for t in (">", "|", "system", "getline", "close(")):
                block("awk programs that write, pipe or run commands can't be checked")
            if exe == "sed" and (SED_CMD.search(prog) or SED_FLAG.search(prog)):
                block("sed w, r and e commands can't be checked")
        in_place = exe == "sed" and any(re.fullmatch(r"-[A-Za-z]*i.*|--in-place.*", w) for w in words[1:])
        return files if in_place else []

    def tar(self, words: list) -> list:
        mode, archive, dirs, args = "", None, [], words[1:]
        for k, a in enumerate(args):
            nxt = args[k + 1] if k + 1 < len(args) else None
            name, eq, val = a.partition("=")
            if name == "--file":
                archive = val if eq else nxt
            elif name == "--directory":
                dirs.append(val if eq else nxt)
            elif name in ("--create", "--append", "--update", "--extract", "--get"):
                mode += "x" if name in ("--extract", "--get") else "c"
            elif a == "-C":
                dirs.append(nxt)
            elif (a.startswith("-") or k == 0) and re.fullmatch(r"-?[A-Za-z]+", a) and not a.startswith("--"):
                mode += a
                if "f" in a:
                    archive = a[a.index("f") + 1:] or nxt
        targets = (dirs or ["."]) if "x" in mode else []
        if any(m in mode for m in "cru"):
            targets.append(archive or "")
        return [t for t in targets if t is not None and t != "-"]

    def find(self, words: list, cwd: str | None):
        k, roots = 1, []
        while k < len(words) and not words[k].startswith("-") and words[k] not in ("(", "!"):
            roots.append(words[k])
            k += 1
        rest = words[k:]
        for j, a in enumerate(rest):
            if a == "-delete":
                for r in roots or ["."]:
                    self.write(r, cwd)
            elif a in ("-fprint", "-fprint0", "-fprintf", "-fls") and j + 1 < len(rest):
                self.write(rest[j + 1], cwd)
            elif a in ("-exec", "-execdir", "-ok", "-okdir"):
                inner = []
                for b in rest[j + 1:]:
                    if b in (";", "+"):
                        break
                    inner.append(b)
                inner, here, flags = self.unwrap(inner, cwd)
                if not inner:
                    block("find -exec without a command")
                self.command(inner, [], here, flags | {"xargs"})

    # -- git, gh, sf --

    def branch(self, cwd: str | None, loc: list, ref: str = "HEAD") -> str | None:
        if cwd is None or self.branch_unknown:
            return None
        try:
            out = subprocess.run(["git", *loc, "rev-parse", "--abbrev-ref", ref], cwd=cwd,
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        b = out.stdout.strip()
        return b if out.returncode == 0 and b and b != "HEAD" else None

    def current(self, cwd: str | None, loc: list, what: str) -> str:
        return self.branch(cwd, loc) or block(f"can't tell the current branch for this {what}")

    def git(self, words: list, cwd: str | None):
        i, loc = 1, []
        while i < len(words) and words[i].startswith("-"):
            w = words[i]
            name, eq, v = w.partition("=")
            if name in ("--git-dir", "--work-tree") and eq:
                i += 1
            elif w in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(words):
                name, v, i = w, words[i + 1], i + 2
            elif w in GIT_GLOBAL_FLAGS:
                i += 1
                continue
            else:
                block(f"git option {name} is not allowed")
            if name == "-C":
                cwd = resolve(v, cwd)
            elif name == "-c":
                self.git_key(v.split("=", 1)[0], write=True)
            else:
                p = resolve(v, cwd)
                loc += [name, p] if p else block(f"can't tell which repo {literal(v)!r} is")
        sub, rest = (words[i], words[i + 1:]) if i < len(words) else ("", [])
        if sub not in GIT_READ | GIT_LOCAL:
            block(f"git {literal(sub).replace(SUB, '$(...)')} is not on the guard's list of git commands")
        if sub not in GIT_READ and (cwd is None or _under(cwd, self.repos)):
            block(f"git {sub} in the main checkout is not allowed; work in the ticket worktree")
        for k, a in enumerate(rest):
            if a.startswith("--output"):
                self.write(a.partition("=")[2] or (rest[k + 1] if k + 1 < len(rest) else ""), cwd)
        getattr(self, "git_" + sub.replace("-", "_"), lambda rest, cwd, loc: None)(rest, cwd, loc)

    def git_checkout(self, rest, cwd, loc):
        self.branch_unknown = True

    git_switch = git_checkout

    def git_push(self, rest, cwd, loc):
        self.push(rest, cwd, loc)

    def git_merge(self, rest, cwd, loc):
        if not any(a in ("--abort", "--quit", "--continue") for a in rest):
            b = self.current(cwd, loc, "merge")
            if b in self.protected:
                block(f"merging into {b} is not allowed; sf merge does that when the flow allows it")

    def git_rebase(self, rest, cwd, loc):
        if any(a in ("-x", "--exec") or a.startswith("--exec=") for a in rest):
            block("git rebase --exec runs commands the guard can't check")
        if self.current(cwd, loc, "rebase") in self.protected:
            block("rebasing a protected branch is not allowed")

    def git_reset(self, rest, cwd, loc):
        if "--hard" in rest and self.current(cwd, loc, "reset") in self.protected:
            block("git reset --hard on a protected branch is not allowed")

    def git_grep(self, rest, cwd, loc):
        if any(a == "-O" or a.startswith(("-O", "--open-files-in-pager")) for a in rest):
            block("git grep -O runs a pager command the guard can't check")

    def git_fetch(self, rest, cwd, loc):
        if any(a.startswith("--upload-pack") for a in rest):
            block("git fetch --upload-pack runs commands the guard can't check")

    def git_branch(self, rest, cwd, loc):
        bad = [a for a in rest if a.startswith("-") and a.partition("=")[0] not in BRANCH_LIST_FLAGS]
        if bad:
            block(f"git branch {bad[0]} is not allowed; list or create branches only")

    def git_tag(self, rest, cwd, loc):
        listing = not rest or any(a in ("-l", "--list") for a in rest)
        if not listing or any(a.startswith("-") and a.partition("=")[0] not in TAG_LIST_FLAGS for a in rest):
            block("git tag may only list tags")

    def git_remote(self, rest, cwd, loc):
        if rest[:1] not in ([], ["-v"], ["--verbose"], ["show"], ["get-url"]):
            block("git remote may only show remotes")

    def git_worktree(self, rest, cwd, loc):
        if rest[:1] != ["list"]:
            block("git worktree may only list worktrees; sf manages them")

    def git_config(self, rest, cwd, loc):
        if any(a in ("--global", "--system", "--file", "-f", "--blob") or a.startswith("--file=") for a in rest):
            block("git config outside the repo is not allowed")
        if any(a.startswith("--get") or a in ("-l", "--list") for a in rest):
            return
        pos = [a for a in rest if not a.startswith("-")]
        if pos[:1] in (["get"], ["list"]):
            return
        if pos[:1] in (["set"], ["unset"]):
            pos = pos[1:]
        self.git_key(pos[0] if pos else "", write=len(pos) > 1 or any(a.startswith("--") for a in rest))

    def git_key(self, key: str, write: bool):
        if write and key.lower() not in GIT_CONFIG_OK:
            block(f"git config {key} is not allowed; only user.name and user.email may be set")

    def push(self, rest: list, cwd: str | None, loc: list):
        if self.dry:
            block("dry run: git push is off")
        pos, k = [], 0
        while k < len(rest):
            a = rest[k]
            k += 1
            if a == "--":
                pos += rest[k:]
                break
            if a.startswith("--"):
                name = a.split("=", 1)[0]
                if name in ("--delete", "--mirror", "--all", "--branches", "--prune", "--receive-pack", "--exec",
                            "--repo", "--tags", "--follow-tags"):
                    block(f"git push {name} is not allowed")
                if name == "--push-option" and "=" not in a:
                    k += 1
            elif a.startswith("-") and len(a) > 1:
                if "d" in a:
                    block("git push -d is not allowed")
                k += "o" in a
            else:
                pos.append(a)
        if any(_dynamic(p) for p in pos):
            block("can't check a push target built at run time")
        targets = []
        if len(pos) < 2:
            targets.append(self.current(cwd, loc, "push"))
            upstream = self.branch(cwd, loc, "@{push}")
            if upstream:
                targets.append(upstream.partition("/")[2])
        for spec in pos[1:]:
            spec = spec[1:] if spec.startswith("+") else spec
            if spec.startswith(":") or "*" in spec:
                block("deleting or wildcard pushes are not allowed")
            src, _, dst = spec.partition(":")
            dst = dst or src
            if dst in ("HEAD", "@"):
                dst = self.current(cwd, loc, "push")
            targets.append(re.sub(r"^(refs/)?heads/", "", dst))
        for t in targets:
            if not re.fullmatch(re.escape(BRANCH_PREFIX) + r"[A-Za-z0-9._/-]+", t) or ".." in t:
                block(f"pushing to {t or 'that ref'} is not allowed; crews push only {BRANCH_PREFIX}* branches")

    def gh(self, words: list):
        pos, k = [], 1
        while k < len(words) and len(pos) < 2:
            a = words[k]
            k += 1
            if a in ("-R", "--repo", "--hostname"):
                k += 1
            elif not a.startswith("-"):
                pos.append(a)
        sub, sub2 = (pos + ["", ""])[:2]
        if sub == "pr" and sub2 in ("create", "merge"):
            block("use sf ship / sf merge instead of gh pr create/merge")
        if sub == "api":
            return self.gh_api(words)
        if sub == "pr" and sub2 in ("comment", "review"):
            if self.dry:
                block(f"dry run: gh pr {sub2} writes to GitHub")
            if sub2 == "review" and (not any(a in ("-c", "--comment") for a in words)
                                     or any(a in ("-a", "--approve", "-r", "--request-changes") for a in words)):
                block("gh pr review is allowed only with --comment")
            return
        if sub and (sub, sub2) not in GH_READ and sub != "search":
            block(f"gh {' '.join(x for x in (sub, sub2) if x)} is not on the guard's read-only list")

    def gh_api(self, words: list):
        for k, a in enumerate(words):
            nxt = words[k + 1] if k + 1 < len(words) else ""
            if a in ("--method", "-X"):
                if nxt.upper() != "GET":
                    block(f"gh api {literal(nxt)} writes to GitHub; use sf")
            elif a.startswith("--method=") and a[9:].upper() != "GET":
                block("gh api with a non-GET method writes to GitHub; use sf")
            elif a.upper() != "-XGET" and not a.startswith("--") and a.startswith("-") and \
                    any(c in a[1:] for c in "XfF"):
                block("gh api with a method, fields or --input writes to GitHub; use sf")
            elif a in ("--field", "--raw-field", "--input") or a.startswith(("--field=", "--raw-field=", "--input=")):
                block("gh api with fields or --input writes to GitHub; use sf")

    def sf(self, words: list):
        pos = [w for w in words[1:] if not w.startswith("-")]
        if any(_dynamic(p) for p in pos[:2]):
            block("can't check an sf command built at run time")
        a, b = (pos + ["", ""])[:2]
        if self.dry and ((a, b) in WRITE_COMMANDS or (a, "") in WRITE_COMMANDS):
            block(f"dry run: sf {' '.join(x for x in (a, b) if x)} is a write")
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
