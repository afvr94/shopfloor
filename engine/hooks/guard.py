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
    from engine.lib import config, paths
    from engine.lib.cli import WRITE_COMMANDS
    _IMPORT_ERROR = None
except Exception as e:  # reported by main(), which then blocks
    _IMPORT_ERROR = e

SUB = "\x00"  # stands in for the output of $( ), backticks or <( ) inside a word
LIT = "\x01"  # a quoted or escaped $, which the shell never expands
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
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
WRITERS = {"rm", "rmdir", "touch", "mkdir", "truncate", "unlink", "shred", "mv", "tee", "cp", "install", "ln",
           "chmod", "chown", "chgrp", "dd", "sed"}
WRITER_VALUES = {"cp": {"-t", "--target-directory", "-S", "--suffix"}, "mv": {"-t", "--target-directory", "-S", "--suffix"},
                 "ln": {"-t", "--target-directory", "-S", "--suffix"},
                 "install": {"-t", "--target-directory", "-S", "--suffix", "-m", "-o", "-g", "--mode", "--owner", "--group"},
                 "mkdir": {"-m", "--mode"}, "touch": {"-r", "-t", "-d", "--reference", "--date"},
                 "truncate": {"-s", "-r", "--size", "--reference"}, "shred": {"-n", "-s", "--iterations", "--size"},
                 "sed": {"-e", "-f", "--expression", "--file"}}
READERS = {"cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "cp", "source", ".", "base64",
           "xxd", "strings", "od", "hexdump", "sed", "awk", "nl", "tac", "sort", "uniq", "diff", "cut", "jq"}
XARGS_UNSAFE = {"git", "gh", "sf", "find", "eval"} | NET_TOOLS | SHELLS | WRITERS | READERS
ENV_OK = {".env.example", ".env.sample", ".env.template"}
GH_READ = {("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"), ("pr", "status"), ("run", "view"),
           ("run", "list"), ("run", "watch"), ("repo", "view"), ("issue", "view"), ("issue", "list"),
           ("auth", "status"), ("workflow", "list"), ("workflow", "view"), ("release", "list"), ("release", "view")}
REDIR = re.compile(r"&>>|&>|<<<|<<-|<<|<>|<&|>&|>>|>\||<|>")
BRACES = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
HOME_VARS = re.compile(r"\$(?:\{(HOME|TMPDIR)\}|(HOME|TMPDIR)\b)")


class Blocked(Exception):
    pass


class Unparseable(Exception):
    pass


def block(why: str):
    raise Blocked(why)


# ---- lexing: words with quotes removed, operators, redirects; substitutions lexed recursively ----

class Word(str):
    subs: list
    quoted: bool


def _word(chars: list, subs: list, quoted: bool) -> Word:
    w = Word("".join(chars))
    w.subs, w.quoted = subs, quoted
    return w


def _subst(s: str, i: int, subs: list) -> int:
    """s[i] starts $( , <( , >( or a backtick: lex the inner command into subs, return the index after it."""
    if s[i] == "`":
        j, buf = i + 1, []
        while j < len(s) and s[j] != "`":
            if s[j] == "\\" and j + 1 < len(s):
                buf.append(s[j + 1] if s[j + 1] in "`\\$" else s[j:j + 2])
                j += 2
            else:
                buf.append(s[j])
                j += 1
        if j >= len(s):
            raise Unparseable
        subs.append(_lex("".join(buf), 0, None)[0])
        return j + 1
    toks, j = _lex(s, i + 2, ")")
    subs.append(toks)
    return j


def _dq(s: str, i: int, end: str | None, chars: list, subs: list) -> int:
    """Double-quoted text (or an unquoted heredoc body when end is None)."""
    while i < len(s):
        c = s[i]
        if end and c == end:
            return i + 1
        if c == "\\" and i + 1 < len(s) and s[i + 1] in '"\\$`\n':
            if s[i + 1] != "\n":
                chars.append(LIT if s[i + 1] == "$" else s[i + 1])
            i += 2
        elif (c == "$" and s[i + 1:i + 2] == "(") or c == "`":
            chars.append(SUB)
            i = _subst(s, i, subs)
        else:
            chars.append(c)
            i += 1
    if end:
        raise Unparseable
    return i


def _heredoc(s: str, i: int, delim: Word, strip: bool) -> int:
    body = []
    while i < len(s):
        j = s.find("\n", i)
        j = len(s) if j < 0 else j
        line, i = s[i:j], j + 1
        if (line.lstrip("\t") if strip else line) == delim:
            break
        body.append(line)
    if not delim.quoted:
        _dq("\n".join(body), 0, None, [], delim.subs)
    return i


def _lex(s: str, i: int, stop: str | None):
    toks, word, subs, quoted = [], None, [], False
    pending, want, depth = [], None, 0

    def flush():
        nonlocal word, subs, quoted, want
        if word is None:
            return
        w = _word(word, subs, quoted)
        toks.append(w)
        if want is not None:
            pending.append((w, want))
            want = None
        word, subs, quoted = None, [], False

    while i < len(s):
        c, nxt = s[i], s[i + 1:i + 2]
        if c in " \t":
            flush()
            i += 1
        elif c == "\n":
            flush()
            toks.append(("op", ";"))
            i += 1
            for delim, strip in pending:
                i = _heredoc(s, i, delim, strip)
            pending.clear()
        elif c == "#" and word is None:
            j = s.find("\n", i)
            i = len(s) if j < 0 else j
        elif c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise Unparseable
            word = (word or []) + list(s[i + 1:j].replace("$", LIT))
            quoted, i = True, j + 1
        elif c == '"':
            word = word or []
            quoted = True
            i = _dq(s, i + 1, '"', word, subs)
        elif c == "\\":
            if nxt != "\n":
                word = (word or []) + [LIT if nxt == "$" else nxt or "\\"]
            i += 2
        elif (c == "$" and nxt == "(") or c == "`" or (c in "<>" and nxt == "("):
            word = (word or []) + [SUB]
            i = _subst(s, i, subs)
        elif c in "<>" or (c == "&" and nxt == ">"):
            if word is not None and "".join(word).isdigit() and not quoted:
                word = None  # a file descriptor number such as the 2 in 2>
            flush()
            op = REDIR.match(s, i).group()
            toks.append(("redir", op))
            if op in ("<<", "<<-"):
                want = op == "<<-"
            i += len(op)
        elif c in ";&|":
            flush()
            two = s[i:i + 2]
            op = two if two in ("&&", "||", "|&", ";;") else c
            toks.append(("op", "|" if op in ("|", "|&") else ";"))
            i += len(op)
        elif c == "(":
            flush()
            toks.append(("op", "("))
            depth += 1
            i += 1
        elif c == ")":
            flush()
            if depth:
                depth -= 1
                toks.append(("op", ")"))
            elif stop == ")":
                return toks, i + 1
            else:
                raise Unparseable
            i += 1
        else:
            word = (word or []) + [c]
            i += 1
    flush()
    if stop or depth or want is not None:
        raise Unparseable
    return toks, i


class Cmd:
    def __init__(self):
        self.words, self.redirs = [], []

    def subs(self):
        return [t for w in self.words + [r[1] for r in self.redirs] for t in w.subs]


def _structure(toks: list) -> list:
    """Group tokens into "(" / ")" markers and pipelines (lists of Cmd)."""
    items, pipe, cmd, redir = [], [], Cmd(), None
    for t in toks:
        if isinstance(t, Word):
            if redir:
                cmd.redirs.append((redir, t))
                redir = None
            else:
                cmd.words.append(t)
            continue
        if redir:
            raise Unparseable
        kind, op = t
        if kind == "redir":
            redir = op
            continue
        if cmd.words or cmd.redirs:
            pipe.append(cmd)
        cmd = Cmd()
        if op == "|":
            continue
        if pipe:
            items.append(pipe)
            pipe = []
        if op in ("(", ")"):
            items.append(op)
    if redir:
        raise Unparseable
    if cmd.words or cmd.redirs:
        pipe.append(cmd)
    if pipe:
        items.append(pipe)
    return items


# ---- paths ----

def resolve(text: str, cwd: str | None) -> str | None:
    """Absolute real path of a word, or None when it depends on run-time values."""
    if SUB in text:
        return None
    if text == "~" or text.startswith("~/"):
        text = os.path.expanduser("~") + text[1:]
    text = HOME_VARS.sub(lambda m: os.environ.get(m.group(1) or m.group(2)) or "$", text)
    if "$" in text:
        return None
    if not os.path.isabs(text):
        if cwd is None:
            return None
        text = os.path.join(cwd, text)
    return os.path.realpath(text)


def _under(p: str, roots) -> bool:
    return any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)


def _env_name(base: str) -> bool:
    return (base == ".env" or base.startswith(".env.")) and base not in ENV_OK


def _dynamic(w: str) -> bool:
    return resolve(w, "/") is None


def _interp(exe: str) -> bool:
    return exe in SHELLS or bool(LANGS.fullmatch(exe))


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


class Guard:
    def __init__(self, cfg: dict):
        self.dry = cfg.get("dryRun", True) is not False
        flow = cfg.get("gitFlow") or {}
        repos = list((cfg.get("repos") or {}).values())
        protected = set(cfg.get("protectedBranches") or []) | {r.get("base") for r in repos}
        tb = flow.get("testBranch")
        self.merge_protected = protected | ({tb} if tb else set())
        self.push_protected = protected | ({tb} if tb and flow.get("type") != "merge-to-test-branch" else set())
        tracker, g = cfg.get("tracker") or {}, cfg.get("guard") or {}
        self.move_targets = {m.split("->")[-1] for m in tracker.get("mayMove", [])}
        home = paths.shopfloor_home()
        secret = [home / "secrets", *g.get("secretFiles", []), *g.get("offLimits", []),
                  tracker.get("tokenFile"), (cfg.get("chat") or {}).get("tokenFile")]
        self.secrets = [os.path.realpath(os.path.expanduser(str(p))) for p in secret if p]
        self.home = os.path.realpath(home)
        self.state = os.path.realpath(paths.crew_state_dir(cfg["id"]))
        roots = [self.state, "/tmp", "/private/tmp", os.environ.get("TMPDIR")]
        self.roots = [os.path.realpath(r) for r in roots if r]
        self.devices = {os.path.realpath(d) for d in ("/dev/null", "/dev/stdout", "/dev/stderr")} | {"/dev/null"}
        self.no_write = [os.path.realpath(paths.ENGINE_DIR)] + self.secrets + \
                        [os.path.realpath(os.path.expanduser(r["path"])) for r in repos if r.get("path")]
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
            block(f"reading secret or off-limits files is not allowed ({text})")

    def write(self, text: str, cwd: str | None):
        p = resolve(text, cwd)
        if p is None:
            block(f"can't tell where {text!r} writes")
        if p in self.devices or p.startswith("/dev/fd/"):
            return
        if _under(p, self.no_write) or (_under(p, [self.home]) and not _under(p, [self.state])):
            block(f"{p} is off limits (engine, crew config, secrets, main checkouts)")
        if not _under(p, self.roots):
            block(f"writes stay in the crew's worktrees or a temp dir, not {p}")

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
        self.script(_lex(text, 0, None)[0], cwd)

    def script(self, toks: list, cwd: str | None):
        stack = []
        for item in _structure(toks):
            if item == "(":
                stack.append(cwd)
            elif item == ")":
                cwd = stack.pop()
            else:
                cwd = self.pipeline(item, cwd)

    def pipeline(self, pipe: list, cwd: str | None):
        new_cwd, decoded = cwd, False
        for k, cmd in enumerate(pipe):
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
        if _dynamic(words[0]) or BRACES.search(words[0]) or any(c in words[0] for c in "*?["):
            block("can't check a command whose name is built at run time")
        if exe in ("git", "gh", "sf") and any(BRACES.search(w) for w in words):
            block(f"brace expansion in {exe} arguments can't be checked")
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
        text = " ".join(args)
        if any(h in text.lower() for h in HOSTS):
            block(NET_MSG)
        if exe not in SHELLS and INLINE_GIT.search(text):
            block("inline code that runs git push/merge or gh can't be checked; run the command directly")
        if exe in SHELLS:
            for k, a in enumerate(args):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a):
                    code = args[k + 1].replace(LIT, "$") if k + 1 < len(args) else block("shell -c without a command")
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
        if any(h in " ".join(words).lower() for h in HOSTS):
            block(NET_MSG)
        if any(SUB in w or "$" in w for w in words[1:]):
            block(f"{exe} with run-time arguments can't be checked")
        for k, a in enumerate(words[:-1]):
            if a in ({"-o", "--output"} if exe == "curl" else {"-O", "--output-document"}):
                self.write(words[k + 1], cwd)

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
        elif exe == "sed":
            in_place = any(re.fullmatch(r"-[A-Za-z]*i.*|--in-place.*", w) for w in words[1:])
            targets = (pos if ("-e" in opts or "-f" in opts or opts) else pos[1:]) if in_place else []
        for t in targets:
            self.write(t, cwd)

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

    def git(self, words: list, cwd: str | None):
        i, loc = 1, []
        while i < len(words) and words[i].startswith("-"):
            w, v = words[i], None
            if w in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(words):
                v, i = words[i + 1], i + 2
            elif w.startswith(("--git-dir=", "--work-tree=")):
                (w, v), i = w.split("=", 1), i + 1
            else:
                i += 1
                continue
            if w == "-C":
                cwd = resolve(v, cwd)
            elif w == "-c":
                self.git_key(v.split("=", 1)[0])
            elif w in ("--git-dir", "--work-tree"):
                p = resolve(v, cwd)
                loc += [w, p] if p else block(f"can't tell which repo {v!r} is")
        sub, rest = (words[i], words[i + 1:]) if i < len(words) else ("", [])
        if sub in ("checkout", "switch"):
            self.branch_unknown = True
        elif sub == "push":
            self.push(rest, cwd, loc)
        elif sub == "merge" and not any(a in ("--abort", "--quit", "--continue") for a in rest):
            b = self.branch(cwd, loc) or block("can't tell the current branch for this merge")
            if b in self.merge_protected:
                block(f"merging into {b} is not allowed; sf merge does that when the flow allows it")
        elif sub == "remote" and rest[:1] and rest[0] in ("set-url", "add"):
            block("changing git remotes is not allowed")
        elif sub == "config":
            self.git_config(rest)

    def git_key(self, key: str):
        k = key.lower()
        if k.startswith(("alias.", "remote.", "url.", "push.", "branch.")) or \
                k in ("core.hookspath", "core.sshcommand", "core.gitproxy"):
            block(f"git config {key} is not allowed (aliases, remotes and push settings)")

    def git_config(self, rest: list):
        if "--global" in rest or "--system" in rest:
            block("git config --global/--system is not allowed")
        if any(a.startswith("--get") or a in ("-l", "--list") for a in rest):
            return
        pos = [a for a in rest if not a.startswith("-")]
        if pos[:1] in (["get"], ["list"]):
            return
        if pos[:1] in (["set"], ["unset"]):
            pos = pos[1:]
        if pos:
            self.git_key(pos[0])

    def push(self, rest: list, cwd: str | None, loc: list):
        if self.dry:
            block("dry run: git push is off")
        force = tags = False
        pos, k = [], 0
        while k < len(rest):
            a = rest[k]
            k += 1
            if a == "--":
                pos += rest[k:]
                break
            if a.startswith("--"):
                name = a.split("=", 1)[0]
                if name in ("--delete", "--mirror", "--all", "--branches", "--prune", "--receive-pack", "--exec", "--repo"):
                    block(f"git push {name} is not allowed")
                force |= name.startswith("--force")
                tags |= name == "--tags"
                if name == "--push-option" and "=" not in a:
                    k += 1
            elif a.startswith("-") and len(a) > 1:
                if "d" in a:
                    block("git push -d is not allowed")
                force |= "f" in a
                k += "o" in a
            else:
                pos.append(a)
        if tags and force:
            block("force-pushing tags is not allowed")
        if any(_dynamic(p) for p in pos):
            block("can't check a push target built at run time")
        targets = []
        if len(pos) < 2:
            b = self.branch(cwd, loc) or block("can't tell the current branch for this push")
            targets += [b, (self.branch(cwd, loc, "@{push}") or "").partition("/")[2]]
        for spec in pos[1:]:
            spec = spec[1:] if spec.startswith("+") else spec
            if spec.startswith(":") or "*" in spec:
                block("deleting or wildcard pushes are not allowed")
            src, _, dst = spec.partition(":")
            dst = dst or src
            if dst in ("HEAD", "@"):
                dst = self.branch(cwd, loc) or block("can't tell the current branch for this push")
            targets.append(dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst)
        for t in targets:
            if t in self.push_protected:
                block(f"pushing to {t} is not allowed; changes land through sf ship")

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
            method = None
            if a in ("-X", "--method"):
                method = words[k + 1] if k + 1 < len(words) else ""
            elif a.startswith("--method="):
                method = a.split("=", 1)[1]
            elif a.startswith("-X"):
                method = a[2:]
            if method is not None and method.upper() != "GET":
                block(f"gh api {method} writes to GitHub; use sf")
            if a in ("-f", "-F", "--field", "--raw-field", "--input") or \
                    a.startswith(("--field=", "--raw-field=", "--input=")) or re.match(r"-[fF].", a):
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
        g = Guard(cfg)
        if tool == "Bash":
            g.check_text(_field(tool_input, "command"), cwd)
        elif tool == "Read":
            g.read(_field(tool_input, "file_path"), cwd)
        elif tool in ("Edit", "Write"):
            g.write(_field(tool_input, "file_path"), cwd)
        elif tool == "NotebookEdit":
            g.write(_field(tool_input, "notebook_path"), cwd)
        elif tool in ("Grep", "Glob"):
            g.search(tool, tool_input, cwd)
        else:
            block(f"the guard doesn't know the {tool} tool")
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
