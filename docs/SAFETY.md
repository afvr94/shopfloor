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

Each ticket's repo is a clone under the crew's `worktrees/<KEY>/<repo>` folder, made from a bundle of `origin` and with its `origin` pointing nowhere. Its `.git` directory sits inside the sandbox's write area (section 4), so the crew owns it entirely and `sf` never runs git in it. Your own checkout is only read, for its `origin` URL. `merge-to-test-branch` merges in a copy of its own under `~/.shopfloor/state/<crew>/merge/`, which crews cannot write.

## 3. The guard

Every tool call in a crew session passes through a `PreToolUse` hook before it runs. It:

- allows only commands on a default-deny list; anything it cannot parse or recognise is blocked
- blocks every `gh` call and `git push`, `git pull` and any `git fetch` that is not from a `.bundle` file in the ticket folder, with a reason naming the `sf` command that does the same (section 4); allows `git bundle create` only into the ticket folder; blocks rebase and `reset --hard` on protected branches (reading the branch from `.git/HEAD` as text, never by running git), and otherwise runs local git only in the crew's worktrees
- blocks direct calls to the Linear and Slack APIs
- blocks commands and file tools that name a secret path: `.env` files, `~/.shopfloor/secrets`, the crew's token files, `guard.secretFiles`, `guard.offLimits` and the credential defaults listed in section 6
- allows writes only inside the crew's worktrees and tickets folders (and the system temp directory); inside those it still blocks `.git` and `.claude` paths and `CLAUDE.md` or `AGENTS.md` at the worktrees root or in a ticket's folder there, which would become instructions for later sessions
- blocks every write to `~/.shopfloor/state/<crew>/ticketstate`, where `sf` keeps each ticket's state, branch and briefing (compared case-insensitively); the check runs before the tool opens the path, so section 4 also covers a path race past it
- blocks MCP tools, WebFetch, inline interpreter code (`python -c`, `node -e` and similar) and anything that opens an editor; WebSearch stays on

The read blocks are honest only about what they see: the guard checks the paths written in a command or tool call. A program that opens a file on its own is checked by the sandbox (section 4), not by the guard.

The guard is a fence against mistakes and prompt-injected commands. **It is not a sandbox.** Code that the repository's own toolchain runs is invisible to it: tests, Makefiles, npm scripts, build steps, and any file the crew writes and then runs. That code runs with the permissions of the crew session, limited only by the next layers. Treat a crew like a contributor you have given a shell inside a restricted account, not like a jailed process.

A tool call the guard blocks fails with a reason, and the crew has to choose another way.

## 4. Claude Code's sandbox

Shopfloor turns on Claude Code's built-in sandbox for every crew session. It is an operating-system limit on what Bash commands can write and read:

- writes only to the crew's worktrees, its tickets folder and the temp directory (`sandbox.filesystem.allowWrite`)
- no reads of the secret set (`sandbox.filesystem.denyRead`): the credential defaults in section 6, `guard.secretFiles`, `guard.offLimits`, `~/.shopfloor/secrets` and the crew's token files
- `failIfUnavailable` and `allowUnsandboxedCommands: false`, so a session fails rather than runs without it
- no network to this machine's loopback address (`sandbox.network.deniedDomains`: `localhost`, `*.localhost`, `127.0.0.1`, `0.0.0.0`, `[::1]`) and no local binding (`allowLocalBinding: false`), so a crew can't reach the board; see section 8

**`sf` runs outside the sandbox** (`sandbox.excludedCommands: ["sf *"]`). It has to: `sf` writes crew state under `~/.shopfloor`, reads the tracker and chat tokens, and talks to GitHub with your `gh` login and git credentials, none of which the sandbox allows. `sf` is the trusted mediator for every side effect. Its surface is small, the guard checks its arguments before it runs (dry-run reporting, `tracker.mayMove`, no secret paths), and it redacts tokens from chat, tracker, pull request and CI log text. A file the crew names (`--body-file`, `--text-file`, a briefing draft) must sit inside the crew's worktrees, tickets folder or the temp directory, is not a secret path, and is read the no-follow way described below: a regular file of at most 1 MiB, never through a link. Claude Code matches excluded commands by the text of the call, so `sf` escapes the sandbox only when it is called by name on its own; a call that chains it with a redirect, a `cd` or `$(...)` stays sandboxed.

Crews never run `gh` or network git themselves. Everything that reaches GitHub goes through `sf`, outside the sandbox:

- `sf pr view <KEY>`: each pull request on the ticket with its state, mergeability, draft flag, head commit, a summary of its checks, the review decision and review comments newer than the ticket's `reviewSeen` (author, body, and file and line for inline comments), as JSON with bodies redacted
- `sf pr log <KEY> <run-id>`: the failed-step log of a CI run on the ticket's branch in one of its pull request repos, redacted and cut to the last 400 lines
- `sf pr reply <KEY> <repo> <number> --body-file <file>`: a comment on one of the ticket's pull requests, redacted (a "would" line in dry run)
- `sf push <KEY> <repo>`: pushes the ticket's branch from the crew's `out-<repo>.bundle`, with the same branch rules as `sf ship` (a "would" line in dry run)
- `sf sync <KEY> <repo>`: fetches origin and writes `in-<repo>.bundle` to the ticket folder; the crew fetches from it and rebases in its own clone

Local git (`status`, `diff`, `log`, `add`, `commit`, `checkout`, a local `rebase`, `bundle create`, a fetch from a bundle) and the repo's own toolchain stay sandboxed. `~/.ssh` and `~/.config/gh` stay on the deny-read list, and SSH or `gh` credentials are only ever used by `sf`. `shopfloor doctor --live` checks each repo's `origin` with `git ls-remote` from the crew's transport repo, the one `sf` pushes from.

### sf never runs git where a crew can write

Sandboxed code can write everything in a clone's `.git`: its config, hooks, `commondir`, attributes, even a repo in the folder above it. Git reads all of that before it does anything, so no checking or pinning of flags makes it safe for `sf` to run git there outside the sandbox. The rule instead is that `sf` never does. Every git command `sf` runs goes through one helper (`engine/lib/gitsafe.py`), which refuses any target directory or path argument inside a crew's `worktrees` or `tickets` folder (compared case-insensitively, after resolving symlinks). It also pins hooks off (`core.hooksPath=/dev/null`), `core.fsmonitor`, pagers, the `ext::` transport and submodule recursion, sets `core.sshCommand=ssh` and your global credential helpers, and sets `GIT_CEILING_DIRECTORIES` so git never looks above the folder it was given.

Code moves between `sf` and a crew only as bundle files in the ticket folder:

- **Transport repo.** For each crew and repo, `sf` keeps a bare repo at `~/.shopfloor/state/<crew>/transport/<repo>.git`, outside the sandbox's write area and blocked by the guard. Its `origin` is your checkout's `origin` URL. It is the only place `sf` fetches or pushes, with explicit `--upload-pack`/`--receive-pack`.
- **The branch is `sf`'s.** At the first `sf worktree prepare` for a ticket, `sf` names the branch `shopfloor/<key>-<title slug>` and records it in its own ticket state. The crew reads it from `sf ticket show`; nothing it writes can change it (`sf ticket set` refuses `branch`, and `sf worktree prepare` takes no branch).
- **New clone** (`sf worktree prepare`): `sf` fetches origin into the transport repo, bundles `origin/<base>` (and the ticket branch if it exists) into a private folder under the state dir, clones that bundle there, checks out the ticket branch, points the clone's `origin` at `file:///nonexistent`, and moves the finished clone into `worktrees/<KEY>/<repo>` by a rename relative to a no-follow handle on `worktrees/<KEY>`; if `worktrees/<KEY>` is a link, `sf` refuses and writes nothing. It also leaves the bundle as `in-<repo>.bundle` in the ticket folder. If the clone already exists, `sf` leaves it alone and only refreshes the bundle; a broken clone is the crew's to rename and prepare again.
- **Sync** (`sf sync`): `sf` fetches origin into the transport repo and writes `in-<repo>.bundle`. The crew, in the sandbox, runs `git fetch <bundle> '+refs/heads/*:refs/remotes/origin/*'` and rebases.
- **Push** (`sf push`, `sf ship`): the crew, in the sandbox, runs `git bundle create <ticket folder>/out-<repo>.bundle <branch>`. `sf` checks the bundle before copying a byte (a regular file with one link, no symlink or FIFO, at most `limits.bundleMaxMB`, 200 MiB by default), streams it out, verifies it, and fetches only the ticket's recorded branch from it into the transport repo; a bundle without that branch is refused and any other refs in it are ignored. It then pushes that branch with `--force-with-lease` against the sha the transport repo last saw on origin, so it never overwrites a push made by someone else.

Reading a bundle runs no repository config, so nothing a crew plants in its clone, in a parent folder or in a bundle runs as `sf`. Every git call `sf` makes has a time limit (300 seconds for network calls, 60 for checking or fetching a crew's bundle); at the limit `sf` kills git and everything it started, and the command fails saying it timed out.

### Nothing outside the sandbox follows a link in a crew folder

The same rule covers plain files. `sf`, the board, the runner, the doctor and the guard hook all run outside the sandbox, and sandboxed code can plant a symlink, a FIFO or a hard link anywhere in its write roots. So:

- **sf's records live where sandboxed code can't write.** Ticket state (stage, branch, PRs, thread), its lock and the validated `briefing.json` the board serves are under `~/.shopfloor/state/<crew>/ticketstate/<KEY>/`, which is outside the sandbox's write roots and blocked by the guard. The ticket folder (`tickets/<KEY>/`) is only the crew's scratch area: plans, specs, answers, drafts and bundles. Nothing in it is trusted, and `sf` never reads its state from there.
- **Every read or write inside a crew folder goes through one helper** (`engine/lib/crewfs.py`). It opens the write root itself, then walks each folder below it with `O_NOFOLLOW`, so a link at any level is refused, never followed. A file must be a regular file with a single link and under a size cap, and is opened without blocking, so a FIFO cannot stall `sf`. New files are created with `O_EXCL` and land by a rename relative to the walked folder, replacing a planted link rather than writing through it. Removing a ticket's clones (`sf worktree remove`) walks the tree the same way and deletes a link itself, never what it points to. The guard reads `.git/HEAD` the same way. A file a crew names to `sf` (`--body-file`, `--text-file`, `sf briefing write`) is an absolute path or one relative to the ticket folder; `..` is refused.
- **Text reaches `gh` on stdin.** Pull request bodies and replies go to `gh` as `--body-file -`, never as a file. A file in the system temp directory could be swapped for a link to one of your files between `sf` writing it and `gh` opening it, because crews can write there. No `sf` code leaves a file in the temp directory for another program to open, and a test fails if one starts to.

### sf locks its own folders, and signs the records it trusts

Claude Code's own Read, Write and Edit tools are not sandboxed: they run as you. The guard checks their paths when the hook runs, and blocks writes to `ticketstate` and the rest of the state dir, but the tool opens the path after that check. A crew that swaps a folder in a path it named for a link at the right moment could aim one of those tools at a file `sf` owns. Two layers handle that race.

**Prevention: sf's folders are read-only while idle.** These are kept at mode `0500`, and the files in them at `0400`:

- the crew's state folder, `~/.shopfloor/state/<crew>` itself (its `worktrees/`, `tickets/`, `transport/` and `merge/` folders stay `0700`: the sandbox and git write inside them)
- `ticketstate/` and each `ticketstate/<KEY>/`
- the files `sf` keeps there: ticket state and briefings, the command queue and its cursor, lessons, the run log, the tick and build locks and the Slack listener's cursor

Claude Code's Write tool writes a temp file beside its target and renames it into place. In a read-only folder neither step can happen, so the write fails with a permission error, whichever path the race aimed it at. `sf`, the board and the listener open a folder (`0700`) only for the length of their own write or append, under a lock on the folder that every `sf` process shares, and lock it again afterwards, also when the write fails. The window is that one write. If you edit one of these files by hand, `chmod u+w` its folder first; the next `sf` write locks it again.

**Detection: signatures.**

- **Signed records:** each ticket's state (stage, branch, PRs, thread), the command queue and its cursor, the lessons store and the validated briefing carry an HMAC-SHA256 signature. Run log lines are display only and are not signed; crew configs are yours to edit and are not signed either.
- **The key** is `~/.shopfloor/secrets/signing/sf.key`: 32 random bytes, made on first use, in a folder of its own. After it is made the folder is `0500` and the key `0400`. `sf` uses the key only if it is a regular file you own, exactly 32 bytes, not a link, with exactly those modes; anything else is refused loudly, and `shopfloor doctor` shows a failing `signing key` row with the fix. The secrets folder is on both the guard's list and the sandbox's deny-read list, so crews can't read the key. A key from an earlier build at `~/.shopfloor/secrets/sf.key` is ignored and can be deleted.
- **Each signature names its record** (crew, and ticket where there is one), so a signed file copied over another fails the check too.
- **The command queue is a chain.** Each line's signature covers its sequence number and the previous line's signature, and the signed cursor names the last acked line by number and signature. A line dropped, repeated, reordered or cut from the end breaks the chain.
- **A record that fails the check is tampered,** and `sf` refuses to act on it. A malformed signature, text that isn't valid JSON or UTF-8, or a value that can't be encoded counts as tampered too; none of them crash `sf` or the board. `sf` writes `record tampered: <file>` to the run log, once per version of the file. A tampered ticket reads as `needs_you` with no branch, PRs or thread, and `sf ticket set` refuses to change it; delete its `state.json` to start that ticket over. A broken command chain or cursor stops `sf commands pending`, `sf commands ack` and new commands until you check it (delete `commands.jsonl` and `commands.cursor` to start the queue over). Tampered lessons are refused, and a tampered briefing is not served. The board flags a tampered ticket, lessons store, briefing, command line or cursor, and keeps working for your other crews.

**What is still possible:**

- Signatures prove `sf` wrote a record, not that you chose what it says. `sf` signs whatever text a crew passes it through `sf ticket set` (a question, a title) or `sf lessons add`. Those are the crew's words, signed.
- Signatures detect a write, they don't prevent it; the folder locks are what prevent it. A crew that won the race inside `sf`'s own write window could still destroy a record, which stops that ticket or the queue until you look.
- Replay: an older signed copy of a record put back in place (an earlier cursor, an earlier ticket state) still passes its signature. It would need a write into a locked folder, which the locks now prevent; nothing else detects it. In the command log, putting back a shorter signed log after newer commands were acked is detected by the chain and the cursor; dropping the newest line before `sf` ever acked it is not.
- Records from Shopfloor builds before signing count as tampered.

Claude Code itself also runs some git commands in a crew session's working folder (for its status line and context), outside the sandbox. Shopfloor does not control those; it relies on Claude Code's own hardening of them, which turns off hooks, fsmonitor, filters and other repository-configured programs on the command line.

On Linux the sandbox needs bubblewrap (`bwrap`) and `socat`; on macOS it uses `sandbox-exec`. `shopfloor doctor` checks for them, and `shopfloor doctor --live` starts one real sandboxed crew session in a temp folder to prove a sandboxed write and an `sf` write both work.

Crew sessions are also isolated from your own Claude Code setup. They start with `--setting-sources ""`, so none of your user, project or local settings, hooks or plugins load (the guard comes from Shopfloor's own plugin, and the sandbox from `--settings`), and with strict MCP configuration, so no MCP servers load. Your Claude login still works: it is not a settings file.

## 5. GitHub branch protection

Turn on GitHub branch protection (or a ruleset) for every protected branch, and require a pull request and your own approval before merge. This is the only layer that still stops a push to `main` if every layer above is bypassed, because it is enforced by GitHub and not by anything on your machine. `shopfloor doctor` fails if a protected branch is not protected.

## 6. Credentials

Crews run unattended with your `gh` login and your Claude login. Anything they can do as you on GitHub, they can attempt. Use a GitHub account or token you are comfortable automating, ideally one with access only to the repos in the crew's config. Tracker and chat tokens live in `0600` files under `~/.shopfloor/secrets`, are referenced by path, and are never printed, logged, passed on a command line or written into config.

Crews may not read these credential paths, through the guard and through the sandbox's deny-read list: `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/.docker/config.json`, `~/.kube`, `~/.gnupg`, `~/.claude/.credentials.json`, `~/.claude.json`, plus `~/.shopfloor/secrets`, the crew's token files and anything in `guard.secretFiles` or `guard.offLimits` (those add to the defaults; they never replace them). Credentials stored elsewhere, such as the macOS keychain or environment variables, are not covered: the sandbox does not hide them from a program the crew's toolchain runs. As a backstop, `sf` redacts anything that looks like a token from chat posts, questions and tracker comments before sending them.

Linear comments are signed with the crew's voice name so they are distinguishable from yours.

## 7. Pausing and approval

While a crew is paused, `sf merge` refuses to merge; that is the only pause rule enforced in code. Not starting builds or claiming new tickets while paused is part of the crew's instructions, like resume. Only your commands in the Slack thread are meant to change it. The instructions a crew follows resume only when an owner `resume` command is pending. Crews cannot write the command queue from the sandbox, its folder is read-only to Claude Code's file tools while `sf` isn't writing it, and the queue is a signed chain (section 4), so a command a crew slips in is refused, not obeyed; crews cannot approve their own work or issue themselves commands. Note that `sf resume` is an ordinary command, so this rule is part of the crew's instructions and not a technical lock; layers 1 to 5 are what hold if a crew ignores it.

## 8. The board

The board listens on `127.0.0.1` only and has no login. Never expose it publicly. To reach it from another device, use a private network: an SSH tunnel (`ssh -L 7420:127.0.0.1:7420 your-machine`) or a VPN, and keep `board.host` on loopback. If you use a tunnel, set `board.linkBase` so links point at the address you use. The board checks the `Host` header and requires a custom header on every change, which defends against attacks from web pages in your own browser; it does not defend against someone who can reach the port.

Crews can't reach it either, as far as Claude Code's sandbox lets Shopfloor say so. Sandboxed commands have no direct route to the network: on macOS the sandbox blocks every connection except the one to its own proxy unless `allowLocalBinding` is on (Shopfloor sets it off), and on Linux a sandboxed command's `localhost` is its own. What is left is the proxy, and in `bypassPermissions` mode, which crews run in, the proxy lets a command reach any host that isn't refused. So the crew settings refuse loopback by name in `deniedDomains`, which Claude Code applies in every permission mode. The proxy matches host names, not resolved addresses; Claude Code also refuses host names that resolve to local addresses unless you allow them. `shopfloor doctor --live` checks the result for real: a sandboxed crew session tries a stand-in for the board on `127.0.0.1` directly, by `localhost` and through the proxy, and the row passes only if the session ran the probe and nothing arrived.

Shopfloor doesn't add a board token on top. A crew that could reach the port could also fetch the page that hands the token to your browser, by sending the same headers a browser sends, so a token would add no protection against crews; the network rule is the control. If `shopfloor doctor --live` ever fails that row, treat the board as reachable by crews.

## What this does not protect against

- A malicious or compromised repository whose scripts run during tests or builds.
- A crew editing files in a worktree in ways you do not review. Review every pull request.
- A crew passing data out through `sf` (a comment or chat post): `sf` runs outside the sandbox. The guard blocks secret paths as arguments and `sf` redacts token-shaped text, but a secret with no recognisable shape that the crew learned some other way could still be posted.
- Credentials kept outside the deny-read list, such as keychain entries a toolchain can reach or tokens in environment variables.
- Anything you allow through `guard` or git flow settings, such as merging automatically into a branch that deploys.
