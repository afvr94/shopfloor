# Safety

A crew is Claude Code running unattended on your machine with your `gh` and Claude logins. This page says what stands between it and damage, layer by layer, and where each layer stops. Read it before you set `"dryRun": false`.

## 1. Dry run

New crews start with `"dryRun": true`, enforced inside the `sf` command that crews use for every write. In dry run, chat and local builds happen. There is no push, no pull request, no pull request reply, no tracker write and no merge: `sf` prints `dry-run: would ...` and records it in the run log instead. The guard lets those `sf` calls through so the crew sees and reports the "would" line. Crews cannot push or comment any other way: the guard blocks `gh` and network git in every mode (section 3). Because nothing moves in the tracker, `sf tracker candidates` leaves out tickets the crew has already staged, so a dry run never claims the same ticket twice. Dry run stays on until you set `"dryRun": false` in the crew config.

## 2. Git flows

`gitFlow.type` decides what a crew may do with a pull request after it is open.

- `pr-only` (default): the crew pushes its `shopfloor/*` branch and opens a pull request. It never merges.
- `squash-on-green`: the crew squash-merges only when all checks are green, the pull request is mergeable and not a draft, and it touches no path matching `gitFlow.mergeDenyPaths`. The merge is pinned to the head commit it checked, so a later push cannot slip in.
- `merge-to-test-branch`: when checks are green, the pull request is not a draft and it touches no denied path, the crew merges it with `--no-ff` into `gitFlow.testBranch`, from its own clone of the repo, and leaves the pull request open. The test branch must not be protected or a repo base branch.

`sf merge` refuses to merge while the crew is paused. `sf ship` and `sf push` push only `shopfloor/*` branches (no `..`), never the test branch, a protected branch or a repo base branch.

Each ticket's repo is a full local clone of your checkout under the crew's `worktrees/<KEY>/<repo>` folder, with its `origin` set to the real remote. It is a clone rather than a git worktree so that its `.git` directory sits inside the sandbox's write area (section 4); your own checkout is only read and fetched. A clone that holds commits found nowhere on `origin` is moved aside into the ticket folder rather than deleted.

## 3. The guard

Every tool call in a crew session passes through a `PreToolUse` hook before it runs. It:

- allows only commands on a default-deny list; anything it cannot parse or recognise is blocked
- blocks every `gh` call and `git push`, `git fetch` and `git pull`, with a reason naming the `sf` command that does the same (section 4); blocks rebase and `reset --hard` on protected branches, and otherwise runs local git only in the crew's worktrees
- blocks direct calls to the Linear and Slack APIs
- blocks commands and file tools that name a secret path: `.env` files, `~/.shopfloor/secrets`, the crew's token files, `guard.secretFiles`, `guard.offLimits` and the credential defaults listed in section 6
- allows writes only inside the crew's worktrees and tickets folders (and the system temp directory); inside those it still blocks `.git` and `.claude` paths, each ticket's `state.json` and `briefing.json` (only `sf` writes those), and `CLAUDE.md` or `AGENTS.md` at the worktrees root or in a ticket's folder there, which would become instructions for later sessions
- blocks MCP tools, WebFetch, inline interpreter code (`python -c`, `node -e` and similar) and anything that opens an editor; WebSearch stays on

The read blocks are honest only about what they see: the guard checks the paths written in a command or tool call. A program that opens a file on its own is checked by the sandbox (section 4), not by the guard.

The guard is a fence against mistakes and prompt-injected commands. **It is not a sandbox.** Code that the repository's own toolchain runs is invisible to it: tests, Makefiles, npm scripts, build steps, and any file the crew writes and then runs. That code runs with the permissions of the crew session, limited only by the next layers. Treat a crew like a contributor you have given a shell inside a restricted account, not like a jailed process.

A tool call the guard blocks fails with a reason, and the crew has to choose another way.

## 4. Claude Code's sandbox

Shopfloor turns on Claude Code's built-in sandbox for every crew session. It is an operating-system limit on what Bash commands can write and read:

- writes only to the crew's worktrees, its tickets folder and the temp directory (`sandbox.filesystem.allowWrite`)
- no reads of the secret set (`sandbox.filesystem.denyRead`): the credential defaults in section 6, `guard.secretFiles`, `guard.offLimits`, `~/.shopfloor/secrets` and the crew's token files
- `failIfUnavailable` and `allowUnsandboxedCommands: false`, so a session fails rather than runs without it

**`sf` runs outside the sandbox** (`sandbox.excludedCommands: ["sf *"]`). It has to: `sf` writes crew state under `~/.shopfloor`, reads the tracker and chat tokens, and talks to GitHub with your `gh` login and git credentials, none of which the sandbox allows. `sf` is the trusted mediator for every side effect. Its surface is small, the guard checks its arguments before it runs (dry-run reporting, `tracker.mayMove`, no secret paths), and it redacts tokens from chat, tracker, pull request and CI log text. A file the crew names (`--body-file`, `--text-file`, a briefing draft) is opened without following a symlink, and `sf` reads it only if its real path is inside the crew's worktrees, tickets folder or the temp directory and not a secret path. Claude Code matches excluded commands by the text of the call, so `sf` escapes the sandbox only when it is called by name on its own; a call that chains it with a redirect, a `cd` or `$(...)` stays sandboxed.

Crews never run `gh` or network git themselves. Everything that reaches GitHub goes through `sf`, outside the sandbox:

- `sf pr view <KEY>`: each pull request on the ticket with its state, mergeability, draft flag, head commit, a summary of its checks, the review decision and review comments newer than the ticket's `reviewSeen` (author, body, and file and line for inline comments), as JSON with bodies redacted
- `sf pr log <KEY> <run-id>`: the failed-step log of a CI run on the ticket's branch in one of its pull request repos, redacted and cut to the last 400 lines
- `sf pr reply <KEY> <repo> <number> --body-file <file>`: a comment on one of the ticket's pull requests, redacted (a "would" line in dry run)
- `sf push <KEY> <repo>`: pushes the ticket clone's branch, with the same branch rules as `sf ship` (a "would" line in dry run)
- `sf sync <KEY> <repo>`: fetches and rebases the ticket branch onto `origin/<base>`; on a conflict it undoes the rebase and lists the conflicted files

Local git (`status`, `diff`, `log`, `add`, `commit`, `checkout`, a local `rebase`) and the repo's own toolchain stay sandboxed. `~/.ssh` and `~/.config/gh` stay on the deny-read list, and SSH or `gh` credentials are only ever used by `sf`. `shopfloor doctor --live` checks each repo's `origin` with `git ls-remote` through the same git `sf` uses.

### Git in ticket clones

Sandboxed code can write a clone's `.git` directory, and `sf` then runs git there outside the sandbox. So every git call `sf` makes in a clone goes through one helper (`engine/lib/gitsafe.py`) that:

- turns off hooks (`core.hooksPath=/dev/null`), `core.fsmonitor`, pagers (`core.pager=cat`), the `ext::` transport and submodule recursion, sets `core.sshCommand=ssh`, and replaces any credential helper with the helpers from your global git config, all on the command line, where they win over any config file; it also sets `GIT_CONFIG_NOSYSTEM=1` and `GIT_TERMINAL_PROMPT=0`, and adds `--no-verify` to push, commit and merge
- checks the clone's `.git/config` before every call against a digest `sf` recorded when it built the clone. The clone is built in a folder the crew cannot write, its digest is stored under `~/.shopfloor/state/<crew>/clones/` (outside the sandbox's write area, and blocked by the guard), and only then is it moved into the worktrees folder. Branch tracking entries and `user.name`/`user.email` may change; anything else (an `include.path`, a new hook path, a rewritten remote) makes `sf` refuse with "clone config changed", log a run line, and the crew hands the ticket to you
- pushes to the push URL of your own checkout's `origin`, with an explicit `HEAD:refs/heads/<branch>` refspec, so the clone's remote settings play no part

A leftover folder `sf` has no digest for is moved aside into the ticket folder without running git in it.

On Linux the sandbox needs bubblewrap (`bwrap`) and `socat`; on macOS it uses `sandbox-exec`. `shopfloor doctor` checks for them, and `shopfloor doctor --live` starts one real sandboxed crew session in a temp folder to prove a sandboxed write and an `sf` write both work.

Crew sessions are also isolated from your own Claude Code setup. They start with `--setting-sources ""`, so none of your user, project or local settings, hooks or plugins load (the guard comes from Shopfloor's own plugin, and the sandbox from `--settings`), and with strict MCP configuration, so no MCP servers load. Your Claude login still works: it is not a settings file.

## 5. GitHub branch protection

Turn on GitHub branch protection (or a ruleset) for every protected branch, and require a pull request and your own approval before merge. This is the only layer that still stops a push to `main` if every layer above is bypassed, because it is enforced by GitHub and not by anything on your machine. `shopfloor doctor` fails if a protected branch is not protected.

## 6. Credentials

Crews run unattended with your `gh` login and your Claude login. Anything they can do as you on GitHub, they can attempt. Use a GitHub account or token you are comfortable automating, ideally one with access only to the repos in the crew's config. Tracker and chat tokens live in `0600` files under `~/.shopfloor/secrets`, are referenced by path, and are never printed, logged, passed on a command line or written into config.

Crews may not read these credential paths, through the guard and through the sandbox's deny-read list: `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/.docker/config.json`, `~/.kube`, `~/.gnupg`, `~/.claude/.credentials.json`, `~/.claude.json`, plus `~/.shopfloor/secrets`, the crew's token files and anything in `guard.secretFiles` or `guard.offLimits` (those add to the defaults; they never replace them). Credentials stored elsewhere, such as the macOS keychain or environment variables, are not covered: the sandbox does not hide them from a program the crew's toolchain runs. As a backstop, `sf` redacts anything that looks like a token from chat posts, questions and tracker comments before sending them.

Linear comments are signed with the crew's voice name so they are distinguishable from yours.

## 7. Pausing and approval

While a crew is paused, `sf merge` refuses to merge; that is the only pause rule enforced in code. Not starting builds or claiming new tickets while paused is part of the crew's instructions, like resume. Only your commands in the Slack thread are meant to change it. The instructions a crew follows resume only when an owner `resume` command is pending. Crews cannot write the command queue, so they cannot approve their own work or issue themselves commands. Note that `sf resume` is an ordinary command, so this rule is part of the crew's instructions and not a technical lock; layers 1 to 5 are what hold if a crew ignores it.

## 8. The board

The board listens on `127.0.0.1` only and has no login. Never expose it publicly. To reach it from another device, use a private network: an SSH tunnel (`ssh -L 7420:127.0.0.1:7420 your-machine`) or a VPN, and keep `board.host` on loopback. If you use a tunnel, set `board.linkBase` so links point at the address you use. The board checks the `Host` header and requires a custom header on every change, which defends against attacks from web pages in your own browser; it does not defend against someone who can reach the port.

## What this does not protect against

- A malicious or compromised repository whose scripts run during tests or builds.
- A crew editing files in a worktree in ways you do not review. Review every pull request.
- A crew passing data out through `sf` (a comment or chat post): `sf` runs outside the sandbox. The guard blocks secret paths as arguments and `sf` redacts token-shaped text, but a secret with no recognisable shape that the crew learned some other way could still be posted.
- Credentials kept outside the deny-read list, such as keychain entries a toolchain can reach or tokens in environment variables.
- Anything you allow through `guard` or git flow settings, such as merging automatically into a branch that deploys.
