---
name: watch
description: Tends one Shopfloor ticket's open pull requests - fixes failing checks, answers review comments, records merged or closed outcomes, and merges when the crew's git flow allows. Use for "/shopfloor:watch <KEY>".
---

# Shopfloor watch

Load `/shopfloor:rules` first. Input is a ticket key in stage `in_review`. Safe to run repeatedly.

## 1. Look
`sf ticket show <KEY>` lists its PRs. For each, `gh pr view <url> --json state,statusCheckRollup,reviewDecision,comments,reviews`. Read-only `gh pr` and `gh run` commands are fine; anything that changes a PR goes through `sf`, except the replies in step 3.

## 2. Failing checks
1. Read the failing check's log (`gh run view <id> --log-failed`).
2. Fix it in the ticket worktree, run the repo's own checks, commit with a Conventional Commit message scoped to the ticket key, and `git push` to the `shopfloor/*` branch.
3. Count the round: `sf ticket set <KEY> --field fixRounds=<n>`.
4. After `limits.maxFixRounds` rounds, stop: `sf chat ask` with what is failing and what you tried, 2 or 3 options and a recommendation. Stage becomes `needs_you`.

A failure that points at CI or deploy config, or at something unrelated to the change, is a deal breaker: ask, do not edit it.

## 3. Review comments
For each new comment: make the fix and push, or answer with `gh pr comment <url> --body-file <file>`. Reply in the configured voice. A request that would trigger a deal breaker, or that grows the change well past the ticket, gets `sf chat ask`.

## 4. Outcomes
- Merged: `sf ticket set <KEY> --stage merged`, post a short note in the thread, and `sf tracker transition <KEY> done` if the crew config allows that move.
- Closed without merging: `sf ticket set <KEY> --stage stopped` and say so in the thread.
- Several PRs: act only when all of them reached the same outcome; otherwise keep tending the open ones.

## 5. Merge
If `sf config` shows a git flow that merges (anything but `pr-only`) and checks pass with no unresolved review, run `sf merge <KEY> <repo> <pr-number>`. It refuses on its own when conditions do not hold; read the refusal, do not argue with it, and post it in the thread.

Never merge while paused (`paused` in `sf config`). In dry run, report the "would merge" line in the thread and stop.
