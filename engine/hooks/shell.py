from __future__ import annotations

import re

SUB = "\x00"  # stands in for run-time text: $( ), backticks, <( ), $'...'
LIT = "\x01"  # a quoted or escaped $, which the shell never expands
LBR = "\x02"  # a quoted or escaped {, which never starts brace expansion
REDIR = re.compile(r"&>>|&>|<<<|<<-|<<|<>|<&|>&|>>|>\||<|>")
OPENERS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time"}


class Unparseable(Exception):
    pass


def literal(text: str) -> str:
    """The text with quoted $ and { restored, for re-lexing (bash -c) or showing."""
    return text.replace(LIT, "$").replace(LBR, "{")


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
        subs.append(lex("".join(buf), 0, None)[0])
        return j + 1
    toks, j = lex(s, i + 2, ")")
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
            chars.append(LBR if c == "{" else c)
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


def lex(s: str, i: int, stop: str | None):
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
            word = (word or []) + list(s[i + 1:j].replace("$", LIT).replace("{", LBR))
            quoted, i = True, j + 1
        elif c == '"':
            word = word or []
            quoted = True
            i = _dq(s, i + 1, '"', word, subs)
        elif c == "\\":
            if nxt != "\n":
                word = (word or []) + [{"$": LIT, "{": LBR}.get(nxt, nxt or "\\")]
            i += 2
        elif c == "$" and nxt == "'":
            j = i + 2
            while j < len(s) and s[j] != "'":
                j += 2 if s[j] == "\\" else 1
            if j >= len(s):
                raise Unparseable
            word = (word or []) + [SUB]  # ANSI-C quoting decodes at run time
            i = j + 1
        elif c == "$" and nxt == '"':
            word = (word or []) + [SUB]
            i = _dq(s, i + 2, '"', word, subs)
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
            prev_word = word is not None
            flush()
            if prev_word or (toks and isinstance(toks[-1], Word) and toks[-1] not in OPENERS):
                raise Unparseable  # a function definition: its body would run under another name
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


def structure(toks: list) -> list:
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
