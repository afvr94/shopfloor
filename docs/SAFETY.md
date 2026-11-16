# Safety

A crew is Claude Code running unattended on your machine with your `gh` and Claude logins. This page says what stands between it and damage, layer by layer, and where each layer stops. Read it before you set `"dryRun": false`.

## 1. Dry run

New crews start with `"dryRun": true`, enforced inside the `sf` command that crews use for every write. In dry run, chat and local builds happen. There is no push, no pull request, no tracker write and no merge: `sf` prints `dry-run: would ...` and records it in the run log instead. Dry run stays on until you set `"dryRun": false` in the crew config.

## 2. Git flows

`gitFlow.type` decides what a crew may do with a pull request after it is open.

- `pr-only` (default): the crew pushes its `shopfloor/*` branch and opens a pull request. It never merges.
- `squash-on-green`: the crew squash-merges only when all checks are green, the pull request is mergeable and not a draft, and it touches no path matching `gitFlow.mergeDenyPaths`. The merge is pinned to the head commit it checked, so a later push cannot slip in.
- `merge-to-test-branch`: when checks are green, the pull request is not a draft and it touches no denied path, the crew merges it with `--no-ff` into `gitFlow.testBranch`, from its own worktree, and leaves the pull request open. The test branch must not be protected or a repo base branch.

Merges never happen while the crew is paused. A crew also refuses to push a protected branch or a repo base branch.

## 3. The guard

Every tool call in a crew session passes through a `PreToolUse` hook before it runs. It:

- allows only commands on a default-deny list; anything it cannot parse or recognise is blocked
- allows git only on the crew's own `shopfloor/*` branches, and never a push to a protected branch
- blocks direct pull request, merge and tracker or chat writes (`gh pr`, calls to the Linear and Slack APIs); these go through `sf`
- blocks reads of secrets, `.env` files and anything in `guard.secretFiles` or `guard.offLimits`
- allows writes only inside the crew's worktrees and tickets folders (and the system temp directory)
- blocks MCP tools, inline interpreter code (`python -c`, `node -e` and similar) and anything that opens an editor

The guard is a fence against mistakes and prompt-injected commands. **It is not a sandbox.** Code that the repository's own toolchain runs is invisible to it: tests, Makefiles, npm scripts, build steps, and any file the crew writes and then runs. That code runs with the permissions of the crew session, limited only by the next layers. Treat a crew like a contributor you have given a shell inside a restricted account, not like a jailed process.

A tool call the guard blocks fails with a reason, and the crew has to choose another way.

## 4. Claude Code's sandbox

Shopfloor turns on Claude Code's built-in sandbox for every crew session. It is an operating-system limit on where Bash commands can write: the crew's worktrees, its tickets folder and the temp directory. Crew sessions are started with the sandbox required and unsandboxed commands disallowed, so a session fails rather than runs without it. On Linux the sandbox needs bubblewrap (`bwrap`) and `socat`; on macOS it uses `sandbox-exec`. `shopfloor doctor` checks for them. Sessions are also started with strict MCP configuration, so no MCP servers are loaded.

## 5. GitHub branch protection

Turn on GitHub branch protection (or a ruleset) for every protected branch, and require a pull request and your own approval before merge. This is the only layer that still stops a push to `main` if every layer above is bypassed, because it is enforced by GitHub and not by anything on your machine. `shopfloor doctor` fails if a protected branch is not protected.

## 6. Credentials

Crews run unattended with your `gh` login and your Claude login. Anything they can do as you on GitHub, they can attempt. Use a GitHub account or token you are comfortable automating, ideally one with access only to the repos in the crew's config. Tracker and chat tokens live in `0600` files under `~/.shopfloor/secrets`, are referenced by path, and are never printed, logged, passed on a command line or written into config. The guard blocks crews from reading that folder.

Linear comments are signed with the crew's voice name so they are distinguishable from yours.

## 7. Pausing and approval

A paused crew merges nothing and starts no builds. Only your commands in the Slack thread can change that. The instructions a crew follows resume only when an owner `resume` command is pending. Crews cannot write the command queue, so they cannot approve their own work or issue themselves commands. Note that `sf resume` is an ordinary command, so this rule is part of the crew's instructions and not a technical lock; layers 1 to 5 are what hold if a crew ignores it.

## 8. The board

The board listens on `127.0.0.1` only and has no login. Never expose it publicly. To reach it from another device, use a private network: an SSH tunnel (`ssh -L 7420:127.0.0.1:7420 your-machine`) or a VPN, and keep `board.host` on loopback. If you use a tunnel, set `board.linkBase` so links point at the address you use. The board checks the `Host` header and requires a custom header on every change, which defends against attacks from web pages in your own browser; it does not defend against someone who can reach the port.

## What this does not protect against

- A malicious or compromised repository whose scripts run during tests or builds.
- A crew editing files in a worktree in ways you do not review. Review every pull request.
- Anything you allow through `guard` or git flow settings, such as merging automatically into a branch that deploys.
