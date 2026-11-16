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
ENV_DENY = re.compile(r"(HOME|PATH|EDITOR|VISUAL|PAGER|SHELL|ENV|BASH_ENV|IFS|CDPATH|PROMPT_COMMAND|NODE_OPTIONS"
                      r"|(XDG|GIT|GH|LD|DYLD)_\w*|PYTHON\w*|npm_config_\w*)", re.IGNORECASE)
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
