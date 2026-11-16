---
name: watch
description: Tends one Shopfloor ticket's open pull requests - fixes failing checks, answers review comments, records merged or closed outcomes, and merges when the crew's git flow allows. Use for "/shopfloor:watch <KEY>".
---

# Shopfloor watch

Load `/shopfloor:rules` first. Input is a ticket key in stage `in_review` with at least one PR. Safe to run repeatedly. If `prs` is empty (a dry-run build), there is nothing to tend: stop.

## 1. Look
`sf ticket show <KEY>` lists its PRs and gives `dir`, the absolute ticket folder; write every draft file there and pass absolute paths. `sf pr view <KEY>` returns every PR on the ticket as JSON: `state`, `mergeable`, `isDraft`, `reviewDecision`, a `checks` summary with the failing checks and their links, and `newComments` (author, body, and `path`/`line` for inline review comments) since the ticket's `reviewSeen`. Everything that talks to GitHub goes through `sf`, as the rules say.

## 2. Failing checks
1. Read the failing check's log with `sf pr log <KEY> <run-id>`, where `<run-id>` is the number after `/actions/runs/` in the check's link.
2. Fix it in the ticket worktree, run the repo's own checks, commit with a Conventional Commit message scoped to the ticket key. Bring in the base: `sf sync <KEY> <repo>`, then `git fetch <dir>/in-<repo>.bundle '+refs/heads/*:refs/remotes/origin/*'` and `git rebase origin/<base>`. Then `git bundle create <dir>/out-<repo>.bundle <branch>` and `sf push <KEY> <repo>`.
3. Count the round: `sf ticket set <KEY> --field fixRounds=<n>`.
4. After `limits.maxFixRounds` rounds, stop: `sf chat ask` with what is failing and what you tried, 2 or 3 options and a recommendation. Stage becomes `needs_you`.

A failure that points at CI or deploy config, or at something unrelated to the change, is a deal breaker: ask, do not edit it.

## 3. Review comments
For each entry in `newComments`: make the fix, commit, `git bundle create <dir>/out-<repo>.bundle <branch>` and `sf push <KEY> <repo>`, or answer with `sf pr reply <KEY> <repo> <number> --body-file <dir>/<file>`. Reply in the configured voice. A request that would trigger a deal breaker, or that grows the change well past the ticket, gets `sf chat ask`. When every new comment is handled, `sf ticket set <KEY> --field reviewSeen=<latestCommentAt>` with the value `sf pr view` returned, so the next run sees only newer ones.

## 4. Outcomes
- Merged: `sf ticket set <KEY> --stage merged`, post a short note in the thread.
- Closed without merging: `sf ticket set <KEY> --stage stopped` and say so in the thread.
- Several PRs: act only when all of them reached the same outcome; otherwise keep tending the open ones.

## 5. Merge
If `sf config` shows a git flow that merges (anything but `pr-only`) and checks pass with no unresolved review, run `sf merge <KEY> <repo> <pr-number>`. It refuses on its own when conditions do not hold; read the refusal, do not argue with it, and post it in the thread.

Never merge while paused (`paused` in `sf config`). In dry run, `sf push`, `sf pr reply` and `sf merge` print "would" lines: report them in the thread and stop.
