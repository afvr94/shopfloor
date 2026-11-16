---
name: tick
description: One scheduled run of a Shopfloor crew - applies owner commands, tends open pull requests, backs off tickets that lost their label, rebuilds cut-off builds, claims and plans new tickets, and hands builds to the runner. Use for "/shopfloor:tick".
---

# Shopfloor tick

Unattended and repeatable: every step must be safe to run twice. Load `/shopfloor:rules` first.

## 1. Commands
1. `sf commands pending` lists owner commands, oldest first. Apply them in order:
   - `pause` runs `sf pause`.
   - `resume` runs `sf resume`. Never resume on your own initiative, only when an owner `resume` command is pending.
   - `lesson` runs `sf lessons add`; `forget` runs `sf lessons forget`.
   - `answer` on a ticket in `needs_you` returns it to the stage before `needs_you`: read `history` in `sf ticket show <KEY>`, take the stage entry just before the latest `needs_you`, set it with `sf ticket set <KEY> --stage <stage>`, and say so in the thread.
2. `sf commands ack <seq>` after the last one you applied.
3. `sf config` again: `paused` may have changed.

## 2. Tend open work
Run `/shopfloor:watch <KEY>` for every ticket in `in_review`, oldest first. Do this even when paused; watch never merges while paused.

## 3. Back off
For every active ticket (`sf ticket list`), `sf tracker get <KEY>`. If the opt-in label is gone or a hold label is present, set the stage to `cancelled` with `sf ticket set`, post once in the thread saying you are standing down, and do not touch the ticket again.

## 4. Interrupted builds
A ticket in stage `building` while `sf build status` says idle was cut off. Each build attempt leaves one `building` entry in `history` (the build skill sets that stage when it starts). If `history` in `sf ticket show <KEY>` already has two `building` entries, do not build a third time: `sf chat ask` with the last failure as the diagnosis (stage becomes `needs_you`), and `sf runs log <KEY> stopped`. Otherwise rebuild it once: first `sf ticket set <KEY> --stage planning` and `sf runs log <KEY> interrupted --note "build cut off, rebuilding"`, then add it to this run's queue for step 6. Moving it back to `planning` is what lets the next build record its own `building` entry.

## 5. New work (skip entirely when paused)
1. `sf tracker candidates` returns eligible tickets. Take at most `limits.maxNewPerTick`, oldest first.
2. Claim each one. Before writing any file, `sf ticket show <KEY>` and read `dir`, the absolute ticket folder; use it for every file below:
   1. `sf tracker transition <KEY> inProgress`
   2. `sf tracker comment <KEY> --body-file <dir>/<file>`: a short note that the crew has picked it up
   3. `sf chat open <KEY>`
   4. `sf ticket set <KEY> --stage claimed`
3. Re-read with `sf tracker get <KEY>`. If someone else started work or a hold label appeared, back off as in step 3.
4. Plan it:
   - Clear and small (one repo, a handful of files, no deal breaker): write `<dir>/plan.md` with the repo, the files you expect to touch, the approach and how you will check it. Then `sf ticket set <KEY> --stage planning` and add the key to this run's queue.
   - Anything else: run `/shopfloor:spec <KEY>`.
5. Check the deal breakers from `/shopfloor:rules` while planning. If one applies, `sf chat ask` and stop on that ticket.

## 6. Hand off builds
"Queued" has no marker in the ticket state. Keep the list of keys you queued during this run (rebuilds first, then new plans), at most `limits.maxParallel`. If `sf build status` shows a runner already working, queue nothing new. Hand exactly that list over in one call: `sf build start <KEY> [<KEY> ...]`. The runner starts one `/shopfloor:build` session per ticket.

## 7. Close
`sf runs log <KEY> <result> --note "<short note>"` for each ticket you moved, results like `claimed`, `queued`, `backed-off`, `stopped`. Nothing happened: end quietly.

In dry run (`sf config` shows `dryRun`), tracker writes and PRs only report what they would do; post that in the thread and carry on.
