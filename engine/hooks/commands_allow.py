"""The crew's Bash command list and the option tables the guard's checkers use."""
from __future__ import annotations

import re

TEXT_TOOLS = {"ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "find", "sed", "awk", "gawk", "sort",
              "uniq", "cut", "tr", "diff", "cmp", "comm", "echo", "printf", "true", "false", "test", "[", "pwd",
              "basename", "dirname", "realpath", "readlink", "stat", "file", "which", "date", "jq", "yq", "env", "seq",
              "expr", "nl", "column", "fold", "paste", "od", "md5", "md5sum", "shasum", "sha256sum"}
FILE_OPS = {"mkdir", "touch", "cp", "mv", "rm", "rmdir", "chmod", "tee"}
BUILTINS = {"cd", "pushd", "popd", "export", "set", ":", "for", "read", "wait"}
TOOLCHAIN = {"python", "python3", "node", "npm", "npx", "pnpm", "yarn", "bun", "deno", "make", "pytest", "go", "cargo",
             "rustc", "uv", "poetry", "pip", "pip3", "tsc", "eslint", "prettier", "ruff", "black", "mypy", "jest",
             "vitest", "bundle", "rake", "ruby", "java", "javac", "mvn", "gradle", "gradlew", "dotnet", "swift",
             "xcodebuild", "php", "composer"}
NET_TOOLS = {"curl", "wget"}
ALLOWED = TEXT_TOOLS | FILE_OPS | BUILTINS | TOOLCHAIN | NET_TOOLS | {"git", "gh", "sf"}
SYSTEM_BIN = {"/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"}
LANGS = re.compile(r"(python[\d.]*|node|nodejs|ruby|deno|bun|php)")
# a toolchain command naming one of these is running something the guard can't follow
HANDOFF = {"git", "gh", "sf", "curl", "wget", "nc", "ncat", "sh", "bash", "zsh", "dash", "ksh", "fish", "perl",
           "sudo", "su", "ssh", "scp", "rsync", "tar", "dd", "xargs", "env", "eval", "osascript"}
# text tools whose arguments are files: a run-time argument could name anything
PATH_ARGS = (TEXT_TOOLS | FILE_OPS) - {"echo", "printf", "test", "[", "true", "false", "expr", "seq", "date", "pwd",
                                       "which", "env"}
WRAPPERS = {"command", "builtin", "time", "nohup", "nice", "timeout"}
WRAP_VALUES = {"nice": {"-n"}, "timeout": {"-s", "-k", "--signal", "--kill-after"}}
KEYWORDS = {"!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}"}

ENV_OK = {"CI", "NODE_ENV", "FORCE_COLOR", "NO_COLOR", "TZ", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE",
          "PYTHONUNBUFFERED", "RUST_BACKTRACE", "DEBUG"}
ENV_DENY = re.compile(r"(HOME|PATH|TMPDIR|EDITOR|VISUAL|PAGER|SHELL|ENV|IFS|CDPATH|PROMPT_COMMAND|NODE_OPTIONS"
                      r"|PS\d|ZDOTDIR|FPATH|(XDG|GIT|GH|LD|DYLD|BASH)_\w*|PYTHON\w*|npm_config_\w*)", re.IGNORECASE)
READ_VALUES = {"-d", "-i", "-n", "-N", "-p", "-t", "-u"}  # -a takes the array name
ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=")

WRITER_VALUES = {"cp": {"-t", "--target-directory", "-S", "--suffix"}, "mv": {"-t", "--target-directory", "-S", "--suffix"},
                 "mkdir": {"-m", "--mode"}, "touch": {"-r", "-t", "-d", "--reference", "--date"},
                 "sed": {"-e", "-f", "--expression", "--file"}, "awk": {"-f", "-v", "-F", "--file"},
                 "gawk": {"-f", "-v", "-F", "--file"},
                 "sort": {"-o", "--output", "-k", "-t", "-S", "-T", "--key", "--field-separator"},
                 "uniq": {"-f", "-s", "-w", "--skip-fields", "--skip-chars", "--check-chars"}}
FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
SED_CMD = re.compile(r"(?:^|[;{}\n])\s*(?:[0-9$]+|/(?:\\.|[^/])*/)?(?:,(?:[0-9$]+|/(?:\\.|[^/])*/))?\s*!?\s*[wWer]\b")
SED_FLAG = re.compile(r"s(.)(?:\\.|(?!\1).)*\1(?:\\.|(?!\1).)*\1[gpIiMm0-9]*[we]")

GIT_READ = {"", "status", "diff", "log", "show", "rev-parse", "ls-files", "grep", "blame", "merge-base", "describe",
            "fetch", "shortlog", "show-ref", "rev-list", "branch", "tag", "remote", "config", "worktree"}
GIT_LOCAL = {"add", "commit", "checkout", "switch", "restore", "reset", "stash", "rebase", "merge", "push",
             "cherry-pick", "mv", "rm"}
_DIFF = {"--stat", "--numstat", "--shortstat", "--name-only", "--name-status", "--cached", "--staged", "--no-index",
         "--color", "--no-color", "--word-diff", "--unified", "--patch", "--no-patch", "--exit-code", "--quiet",
         "--summary", "--dirstat", "--check", "--ignore-all-space", "--ignore-space-change", "--diff-filter",
         "--relative", "--find-renames", "--merge-base", "--compact-summary", "--minimal", "--histogram",
         "--patience", "--no-ext-diff", "--text", "--binary", "--abbrev", "--full-index", "--no-renames", "--raw"}
_LOG = _DIFF | {"--oneline", "--graph", "--decorate", "--no-decorate", "--all", "--format", "--pretty",
                "--max-count", "--since", "--until", "--after", "--before", "--author", "--committer", "--grep",
                "--follow", "--reverse", "--first-parent", "--no-merges", "--merges", "--date", "--abbrev-commit",
                "--left-right", "--cherry-pick", "--ancestry-path", "--topo-order", "--date-order", "--source",
                "--skip", "--branches", "--remotes", "--tags", "--invert-grep", "--all-match", "--show-signature",
                "--name-only", "--no-walk"}
# git long options per subcommand, matched exactly: git accepts unambiguous abbreviations of every other option
GIT_LONG = {
    "": {"--version", "--help"}, "status": {"--short", "--branch", "--porcelain", "--long", "--untracked-files",
                                            "--ignored", "--show-stash", "--ahead-behind", "--no-ahead-behind",
                                            "--column", "--no-column", "--renames", "--no-renames", "--verbose"},
    "diff": _DIFF, "log": _LOG, "show": _LOG, "shortlog": {"--summary", "--numbered", "--email", "--all"},
    "rev-parse": {"--abbrev-ref", "--show-toplevel", "--git-dir", "--verify", "--short", "--symbolic-full-name",
                  "--is-inside-work-tree", "--show-prefix", "--git-common-dir", "--absolute-git-dir", "--show-cdup",
                  "--quiet"},
    "ls-files": {"--others", "--exclude-standard", "--cached", "--modified", "--deleted", "--ignored", "--stage",
                 "--error-unmatch", "--full-name", "--directory", "--exclude"},
    "grep": {"--line-number", "--ignore-case", "--word-regexp", "--count", "--files-with-matches", "--name-only",
             "--cached", "--untracked", "--extended-regexp", "--fixed-strings", "--perl-regexp", "--invert-match",
             "--heading", "--break", "--show-function", "--context", "--after-context", "--before-context",
             "--max-depth", "--all-match", "--quiet", "--null", "--files-without-match"},
    "blame": {"--porcelain", "--line-porcelain", "--show-email", "--date", "--incremental"},
    "merge-base": {"--is-ancestor", "--all", "--fork-point", "--octopus"},
    "describe": {"--tags", "--always", "--abbrev", "--long", "--dirty", "--exact-match", "--match", "--contains",
                 "--all"},
    "fetch": {"--all", "--prune", "--tags", "--no-tags", "--depth", "--unshallow", "--quiet", "--verbose",
              "--dry-run", "--prune-tags", "--force"},
    "show-ref": {"--heads", "--tags", "--verify", "--quiet", "--hash", "--head"},
    "rev-list": {"--count", "--all", "--max-count", "--left-right", "--first-parent", "--reverse", "--since",
                 "--until", "--merges", "--no-merges"},
    "branch": {"--all", "--remotes", "--list", "--verbose", "--show-current", "--contains", "--no-contains",
               "--merged", "--no-merged", "--sort", "--format", "--quiet", "--color", "--no-color", "--points-at",
               "--column", "--no-column"},
    "tag": {"--list", "--contains", "--no-contains", "--points-at", "--sort", "--merged", "--no-merged", "--format",
            "--column"},
    "remote": {"--verbose"},
    "config": {"--get", "--get-all", "--get-regexp", "--list", "--show-origin", "--show-scope", "--bool", "--int",
               "--type", "--default", "--null", "--name-only", "--local"},
    "worktree": {"--porcelain"},
    "add": {"--all", "--update", "--verbose", "--dry-run", "--force", "--intent-to-add", "--ignore-removal",
            "--no-all"},
    "commit": {"--all", "--message", "--amend", "--no-edit", "--quiet", "--signoff", "--no-verify", "--allow-empty",
               "--file", "--author", "--date", "--fixup", "--verbose", "--gpg-sign", "--no-gpg-sign"},
    "checkout": {"--quiet", "--force", "--track", "--no-track", "--detach", "--ours", "--theirs", "--orphan"},
    "switch": {"--create", "--detach", "--guess", "--no-guess", "--track", "--no-track", "--quiet",
               "--discard-changes", "--orphan"},
    "restore": {"--source", "--staged", "--worktree", "--ours", "--theirs", "--quiet"},
    "reset": {"--soft", "--mixed", "--hard", "--keep", "--merge", "--quiet"},
    "stash": {"--include-untracked", "--keep-index", "--all", "--quiet", "--message", "--staged", "--index"},
    "rebase": {"--onto", "--continue", "--abort", "--skip", "--autostash", "--no-autostash", "--quit", "--keep-base",
               "--root", "--rebase-merges", "--no-verify", "--update-refs", "--force-rebase", "--ignore-date",
               "--committer-date-is-author-date", "--strategy", "--strategy-option"},
    "merge": {"--no-ff", "--ff-only", "--ff", "--squash", "--no-commit", "--abort", "--continue", "--quit", "--message",
              "--no-edit", "--strategy", "--strategy-option", "--stat", "--no-stat", "--quiet", "--no-verify",
              "--allow-unrelated-histories", "--log", "--signoff"},
    "cherry-pick": {"--continue", "--abort", "--skip", "--quit", "--no-commit", "--signoff", "--mainline",
                    "--strategy", "--strategy-option", "--allow-empty", "--ff", "--keep-redundant-commits"},
    "push": {"--set-upstream", "--force-with-lease", "--force", "--force-if-includes", "--dry-run", "--quiet",
             "--verbose", "--no-verify", "--porcelain", "--atomic", "--progress", "--push-option"},
    "mv": {"--force", "--dry-run", "--verbose"},
    "rm": {"--cached", "--force", "--recursive", "--quiet", "--dry-run", "--ignore-unmatch"},
}
# short options that open an editor, a patch prompt or run commands
GIT_SHORT_DENY = {"merge": set("e"), "cherry-pick": set("e"), "checkout": set("p"), "restore": set("p"),
                  "stash": set("p"), "reset": set("p"), "rebase": set("xi")}
GIT_GLOBAL_FLAGS = {"--no-pager", "-P", "--no-optional-locks", "--version", "--help", "--literal-pathspecs"}
GIT_CONFIG_OK = {"user.name", "user.email"}
# commit and add take messages through -m/-F only: anything that opens an editor or a prompt is out
COMMIT_SHORT, COMMIT_LONG = set("amqsvnFS"), {"--all", "--message", "--amend", "--no-edit", "--quiet", "--signoff",
                                              "--no-verify", "--allow-empty", "--file", "--author", "--date",
                                              "--fixup", "--verbose", "--gpg-sign", "--no-gpg-sign"}
ADD_SHORT, ADD_LONG = set("AvnfuN"), {"--all", "--update", "--verbose", "--dry-run", "--force", "--intent-to-add",
                                      "--ignore-removal", "--no-all"}
MESSAGE_OPTS = {"-m", "--message", "-F", "--file"}
BRANCH_LIST_FLAGS = {"-a", "--all", "-r", "--remotes", "-l", "--list", "-v", "-vv", "--verbose", "--show-current",
                     "--contains", "--no-contains", "--merged", "--no-merged", "--sort", "--format", "-q", "--quiet",
                     "--color", "--no-color", "--points-at", "--column", "--no-column"}
TAG_LIST_FLAGS = {"-l", "--list", "-n", "--contains", "--no-contains", "--points-at", "--sort", "--merged",
                  "--no-merged", "--format", "--column"}
GH_READ = {("pr", "view"), ("pr", "list"), ("pr", "checks"), ("pr", "diff"), ("pr", "status"), ("run", "view"),
           ("run", "list"), ("run", "watch"), ("repo", "view"), ("issue", "view"), ("issue", "list"),
           ("auth", "status"), ("workflow", "list"), ("workflow", "view"), ("release", "list"), ("release", "view")}


def positionals(words: list, value_opts: set):
    """(positional words, {option: value}) for words[1:], knowing which options take a value."""
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
        elif not a.startswith("--"):
            for j, ch in enumerate(a[1:], 1):
                if "-" + ch in value_opts:
                    opts["-" + ch] = a[j + 1:] or (words[k] if k < len(words) else "")
                    k += 0 if a[j + 1:] else 1
                    break
    return pos, opts


def short_value(words: list, k: int, ch: str) -> str | None:
    """The value of short option ch inside the cluster words[k] (-sSo file, -ofile), or None."""
    a = words[k]
    if a.startswith("--") or not a.startswith("-") or ch not in a[1:]:
        return None
    rest = a[a.index(ch, 1) + 1:]
    return rest or (words[k + 1] if k + 1 < len(words) else "")


def short_chars(a: str) -> set:
    """The letters of a short-option cluster such as -am, or an empty set."""
    return set(a[1:]) if a.startswith("-") and not a.startswith("--") and len(a) > 1 else set()


class Blocked(Exception):
    pass


def block(why: str):
    raise Blocked(why)


def abbreviates(name: str, options) -> bool:
    """name is one of options or an unambiguous-looking prefix of one (getopt and optparse accept those)."""
    return len(name) > 3 and any(o.startswith(name) for o in options)


NPX_TOOLS = {"tsc", "eslint", "prettier", "jest", "vitest", "playwright"}
RUNNER_SUBCOMMANDS = {"npm": {"exec", "x", "explore", "edit"}, "pnpm": {"exec", "dlx", "x"}, "yarn": {"dlx", "exec"},
                      "bun": {"x"}, "uv": {"run", "tool"}, "poetry": {"run", "shell"}, "bundle": {"exec"}}
MAKE_DENY_LONG = {"--file", "--makefile", "--eval", "--directory", "--include-dir", "--environment-overrides"}
PY_MODULES = {"pytest", "unittest", "pip", "black", "ruff", "mypy", "json.tool", "venv"}
PIP_DENY = {"--target", "--prefix", "--root", "--user", "--src", "--editable"}
# tools that rewrite, or emit output next to, the paths they are given
IN_PLACE = {"prettier", "eslint", "black", "ruff", "tsc"}
# options whose value names code or config a tool loads (matched case-insensitively, abbreviations included)
CODE_LONG = {"--config", "--config-file", "--rootdir", "--root", "--dir", "--setupfiles", "--setupfilesafterenv",
             "--globalsetup", "--globalteardown", "--project", "--projects", "--rulesdir",
             "--resolve-plugins-relative-to", "--plugin", "--require", "--import", "--loader",
             "--experimental-loader", "--preload", "--testrunner", "--resolver", "--transform", "--preset",
             "--confcutdir", "--testenvironment", "--environment", "--snapshotresolver", "--watchplugins",
             "--reporter", "--reporters", "--import-map", "--env-file"}
CODE_SHORT = {"jest": "c", "vitest": "cr", "eslint": "c", "tsc": "p", "pytest": "c", "node": "r", "nodejs": "r",
              "bun": "r", "ruby": "rIC", "php": "fcd"}
# short letters that take program text from argv; python is parsed exactly in python_args
INLINE_SHORT = {"node": "ep", "nodejs": "ep", "ruby": "e", "php": "rRBEF", "bun": "ep", "deno": ""}
INLINE_LONG = {"--eval", "--eval-file", "--print"}
PY_VALUE = set("WX")
# pure-output options: their value may also point into a temp dir; every other toolchain path stays in the worktree
OUTPUT_OPTS = {"--junitxml", "--junit-xml", "--cov-report", "--basetemp", "-o", "--output", "--out-dir", "--outdir"}
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:(?!:)")  # data:, file:, https:, git+ssh: ... (not pytest's a.py::t)
SCHEME_OK = {("pytest", "-p")}  # -p no:cacheprovider names a plugin
# options that run a program or inline code: go -exec/-toolexec/-overlay, rake -e/-p/-E
EXEC_SHORT = {"rake": "epE"}
EXEC_LONG = {"go": {"-exec", "-toolexec", "-overlay"},
             "rake": {"--eval", "--execute", "--execute-print", "--execute-continue"}}
CURL_OUT = {"-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", "--trace", "--trace-ascii", "--stderr",
            "--output-dir"}
WGET_OUT = {"-O", "--output-document", "-o", "--output-file", "-a", "--append-output", "-P", "--directory-prefix"}
# long options that write, run code or take a value, per command: getopt accepts any unambiguous prefix
LONG_OPTS = {
    "sed": {"--in-place", "--expression", "--file"},
    "awk": {"--source", "--include", "--file", "--load", "--exec"},
    "sort": {"--compress-program", "--output", "--key", "--field-separator", "--temporary-directory"},
    "cp": {"--link", "--symbolic-link"},
    "curl": CURL_OUT | {"--config", "--remote-name", "--remote-name-all", "--upload-file", "--data", "--form"},
    "wget": WGET_OUT | {"--input-file", "--execute", "--config", "--post-file", "--body-file"},
    "gh": {"--method", "--field", "--raw-field", "--input", "--approve", "--request-changes", "--repo"},
}
LONG_OPTS["gawk"] = LONG_OPTS["awk"]
for _exe, _vals in WRITER_VALUES.items():
    LONG_OPTS[_exe] = LONG_OPTS.get(_exe, set()) | {o for o in _vals if o.startswith("--")}


def expand_long(exe: str, args: list) -> list:
    """Spell out abbreviated long options (--in-pl=x is --in-place=x) so exact-name checks see them."""
    opts, out = LONG_OPTS.get(exe), []
    for k, a in enumerate(args):
        if a == "--":
            return out + args[k:]
        name, eq, val = a.partition("=")
        full = sorted(o for o in opts or () if o.startswith(name)) if a.startswith("--") and len(name) > 2 else []
        out.append(full[0] + eq + val if full and name not in opts else a)
    return out


def looks_like_path(v: str) -> bool:
    return "/" in v or v.startswith(("~", "."))


def option_values(args: list) -> list:
    """Path-like values any option could carry (--opt=v, --opt v, -x v), with the v of k=v too."""
    vals = []
    for k, a in enumerate(args):
        if not a.startswith("-") or a in ("-", "--"):
            continue
        name, eq, val = a.partition("=")
        if eq:
            vals.append(val)
        elif k + 1 < len(args) and not args[k + 1].startswith("-"):
            vals.append(args[k + 1])
    vals += [v.partition("=")[2] for v in vals if "=" in v]
    return [v for v in vals if looks_like_path(v)]


def code_values(exe: str, args: list) -> list:
    """Values of the options that name code or config the tool loads."""
    out = []
    for k, a in enumerate(args):
        name, eq, val = a.partition("=")
        if a.startswith("--") and abbreviates(name.lower(), CODE_LONG):
            out.append(val if eq else args[k + 1] if k + 1 < len(args) else "")
        elif not a.startswith("--"):
            out += [short_value(args, k, ch).lstrip("=") for ch in CODE_SHORT.get(exe, "")
                    if short_value(args, k, ch) is not None]
    return out + [v.partition("=")[2] for v in out if "=" in v]


def toolchain(exe: str, words: list, write, code) -> None:
    """Checks for the build and test tools: runners that exec other programs block, outputs stay in write roots,
    and the code and config they load come from the worktree."""
    args = words[1:]
    pos = [a for a in args if not a.startswith("-")]
    for a in args:
        if any(c in a for c in "\\'\"$\x00\x01"):
            block(f"{exe} arguments with quotes, backslashes or $ can't be checked")
    if exe == "npx":
        rest = [a for a in args if a not in ("-y", "--yes", "--no-install")]
        if not rest or rest[0] not in NPX_TOOLS:
            block(f"npx may only run {', '.join(sorted(NPX_TOOLS))}")
        return toolchain(rest[0], rest, write, code)
    if pos[:1] and pos[0] in RUNNER_SUBCOMMANDS.get(exe, ()):
        block(f"{exe} {pos[0]} runs another program the guard can't follow")
    if exe in ("npm", "pnpm", "yarn") and any(a in ("-c", "--call") or abbreviates(a.partition("=")[0], {
            "--call", "--script-shell", "--shell"}) for a in args):
        block(f"{exe} -c/--script-shell runs a command the guard can't follow")
    if exe == "go" and pos[:1] == ["run"]:
        for p in pos[1:]:
            if "@" in p:
                block("go run of a remote module is not allowed")
            code(p)
    if exe == "make":
        make(args)
    if exe in ("pip", "pip3"):
        pip(args)
    exec_options(exe, args)
    toolchain_paths(exe, args, write, code)
    for v in code_values(exe, args):
        code(v)
    for p in pos:
        if exe in IN_PLACE and p not in ("check", "format"):
            write(p)


def exec_options(exe: str, args: list) -> None:
    for a in args:
        name = a.partition("=")[0]
        if name in EXEC_LONG.get(exe, ()) or a.startswith("--") and abbreviates(name, EXEC_LONG.get(exe, ())) or \
                short_chars(a) & set(EXEC_SHORT.get(exe, "")):
            block(f"{exe} {name} runs a program or inline code the guard can't check")


def toolchain_paths(exe: str, args: list, write, code) -> None:
    """Every path a toolchain is given stays in the worktree; pure-output options may also use a temp dir.
    URL-like values (data:, file:, https:, git+...) block."""
    k = 0
    while k < len(args):
        a, prev = args[k], args[k - 1] if k else ""
        k += 1
        name, eq, val = a.partition("=")
        if a.startswith("-") and name.lower() in OUTPUT_OPTS:
            if not eq:
                val, k = (args[k] if k < len(args) else ""), k + 1
            out = val.partition(":")[2] if name.lower() == "--cov-report" else val
            for v in (out, out.partition("=")[2]):
                write(v)
            continue
        if a.startswith("-o") and not a.startswith("--") and len(a) > 2:
            write(a[2:])
            continue
        vals = [val] if a.startswith("-") and eq else [] if a.startswith("-") else [a]
        if a.startswith("-") and not a.startswith("--") and not eq and len(a) > 2:
            vals.append(a[2:])  # -f/tmp/x: a glued value
        for v in vals + [v.partition("=")[2] for v in vals if "=" in v]:
            if SCHEME.match(v) and (exe, prev) not in SCHEME_OK:
                block(f"{exe} may not load {v.partition(':')[0]}: URLs; use files in the worktree")
            if looks_like_path(v):
                code(v)


def make(args: list) -> None:
    for a in args:
        if (a.startswith("--") and abbreviates(a.partition("=")[0], MAKE_DENY_LONG)) or \
                short_chars(a) & set("fECIe") and not a.startswith("--"):
            block("make may only run the worktree's Makefile (no -f, -C, -I, -e or --eval)")
        if not a.startswith("-") and ASSIGN.match(a):
            block("make variables can't be set from the command line; they can replace SHELL or load makefiles")


def pip(args: list) -> None:
    pos = [a for a in args if not a.startswith("-")]
    if pos[:1] and pos[0] not in ("install", "list", "show", "freeze"):
        block(f"pip {pos[0]} is not allowed; install, list, show or freeze only")
    for a in args:
        if a == "-t" or a.startswith("-t") and not a.startswith("--") or abbreviates(a.partition("=")[0], PIP_DENY):
            block("pip may only install into the active environment")


def python_module(mod: str, rest: list, write, code) -> None:
    """python -m: only the listed modules, checked as if run directly."""
    if mod not in PY_MODULES:
        block(f"python -m {mod} is not allowed")
    pos = [a for a in rest if not a.startswith("-")]
    if mod == "json.tool" and len(pos) > 1:
        block("python -m json.tool may only print")
    for p in pos:
        if mod == "venv":
            write(p)
        if mod == "unittest":
            code(p)
    toolchain(mod, [mod, *rest], write, code)


def python_args(args: list, write, code) -> None:
    """Parse python's own options exactly: -c is inline code, -m hands off, the first word is the script."""
    k = 0
    while k < len(args):
        a = args[k]
        k += 1
        if a == "--" or not a.startswith("-") or a == "-":
            script = args[k] if a == "--" and k < len(args) else a
            return code(script) if script not in ("-", "--") else None
        if a.startswith("--"):
            k += a == "--check-hash-based-pycs"
            continue
        for j, ch in enumerate(a[1:], 1):
            glued = a[j + 1:]
            if ch == "c":
                block("inline interpreter code can't be checked; put it in a file in the worktree")
            if ch == "m":
                mod = glued or (args[k] if k < len(args) else "")
                return python_module(mod, args[k:] if glued else args[k + 1:], write, code)
            if ch in PY_VALUE:
                k += not glued
                break


def interpreter(exe: str, args: list, write, code) -> None:
    """Interpreters run only program files from the worktree: inline code is default-deny."""
    if exe.startswith("python"):
        return python_args(args, write, code)
    inline = set(INLINE_SHORT.get(exe, ""))
    for a in args:
        if a == "--":
            break
        if short_chars(a) & inline or a.startswith("--") and abbreviates(a.partition("=")[0], INLINE_LONG):
            block("inline interpreter code can't be checked; put it in a file in the worktree")
    pos = [a for a in args if not a.startswith("-")]
    # the grammar of these interpreters is large: every path-like word must be worktree code
    words = [a for a in args if not a.startswith("-")] + option_values(args) + code_values(exe, args) + \
        [a[2:] for a in args if a.startswith("-") and not a.startswith("--")]
    for w in words:
        if SCHEME.match(w):
            block(f"{exe} may not load {w.partition(':')[0]}: URLs; use files in the worktree")
    for p in pos[:1] + [w for w in words if looks_like_path(w)]:
        code(p)
