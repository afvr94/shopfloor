---
name: tick
description: One scheduled run of a Shopfloor crew - applies owner commands and delivers answers, tends open pull requests, backs off tickets that lost their label, rebuilds cut-off builds, sweeps ready tickets, claims and plans new tickets, and hands builds to the runner. Use for "/shopfloor:tick".
---

# Shopfloor tick

Unattended and repeatable: every step must be safe to run twice. Load `/shopfloor:rules` first.

"Queued" has no marker in the ticket state. A ticket waits for a build at stage `planning` with a `plan.md` or a settled `spec.md`; step 5 sweeps every such ticket into the queue on every tick, so nothing is lost when a tick cannot hand one off.

## 1. Commands
1. `sf commands pending` lists owner commands, oldest first, each with its ticket `key`. Apply them in order:
   - `pause` runs `sf pause`.
   - `resume` runs `sf resume`. Never resume on your own initiative, only when an owner `resume` command is pending.
   - `lesson` runs `sf lessons add`; `forget` runs `sf lessons forget`.
   - `answer`: see step 1.2.
2. An `answer` is saved before anything else. `sf ticket show <KEY>` gives `dir`, `question`, `stage` and `history`. Append to `<dir>/answers.md` a dated entry with the question (the `question` field) and the answer text. For a numbered choice, write the text of that option from `<dir>/question.md`. An answer on a ticket that is not in `needs_you` is only saved. On a ticket in `needs_you`, take the stage entry in `history` just before the latest `needs_you` and route it:
   - `planning` and `<dir>/spec.md` exists: the owner answered a spec question. Run `/shopfloor:spec <KEY>` now; it folds the answer into the spec and leaves the ticket at `planning` (or asks again only if a new question appears).
   - `planning` without a spec, or `building`: `sf ticket set <KEY> --stage planning`. Never set `building` yourself; the build skill does that when it starts. Step 5 queues it.
   - `claimed`: `sf ticket set <KEY> --stage claimed` and plan it now as in step 6.4 (even when paused; planning builds nothing).
   - `waiting`: `sf ticket set <KEY> --stage waiting`; step 5 re-checks it.
   - Anything else: set that stage with `sf ticket set` and post a short note in the thread.
3. `sf commands ack <seq>` after the last one you applied.
4. `sf config` again: `paused` may have changed.

## 2. Tend open work
Run `/shopfloor:watch <KEY>` for every ticket in `in_review`, oldest first. Do this even when paused; watch never merges while paused.

## 3. Back off
For every active ticket (`sf ticket list`), `sf tracker get <KEY>`. If the opt-in label is gone or a hold label is present, set the stage to `cancelled` with `sf ticket set`, post once in the thread saying you are standing down, and do not touch the ticket again.

## 4. Interrupted builds
A ticket in stage `building` while `sf build status` says idle was cut off. Each build attempt leaves one `building` entry in `history` (the build skill sets that stage when it starts). If `history` in `sf ticket show <KEY>` already has two `building` entries after its most recent `needs_you` entry (all of them if there is none), do not build a third time: `sf chat ask` with the last failure as the diagnosis (stage becomes `needs_you`), and `sf runs log <KEY> stopped`. Otherwise rebuild it once: `sf ticket set <KEY> --stage planning` and `sf runs log <KEY> interrupted --note "build cut off, rebuilding"`; step 5 queues it. Moving it back to `planning` is what lets the next build record its own `building` entry.

## 5. Ready sweep (every tick)
Build this run's queue from `sf ticket list`, oldest first:
- every ticket at `planning` with `<dir>/plan.md`, or with `<dir>/spec.md` whose open questions are all settled (none left, or each marked decided);
- every ticket at `waiting` whose wait is over (what it waits on, named in its plan or spec, is done): `sf ticket set <KEY> --stage planning` first. A wait that is not over stays as it is.

A ticket the runner cannot take this tick (runner busy, or over `limits.maxParallel`) stays at `planning`, and the next tick's sweep queues it. Nothing else is needed to pick it up later.

## 6. New work (skip entirely when paused)
1. `sf tracker candidates` returns eligible tickets. Take at most `limits.maxNewPerTick`, oldest first.
2. Claim each one. Before writing any file, `sf ticket show <KEY>` and read `dir`, the absolute ticket folder; use it for every file below:
   1. `sf tracker transition <KEY> inProgress`
   2. `sf tracker comment <KEY> --body-file <dir>/<file>`: a short note that the crew has picked it up
   3. `sf chat open <KEY>`
   4. `sf ticket set <KEY> --stage claimed`
3. Re-read with `sf tracker get <KEY>`. If someone else started work or a hold label appeared, back off as in step 3.
4. Plan it. Read `<dir>/answers.md` first if it exists; the owner's answers win. Check the deal breakers from `/shopfloor:rules` before you write anything: if one applies, `sf chat ask` and stop on that ticket.
   - Clear and small (one repo, a handful of files, no deal breaker): write `<dir>/plan.md` with the repo, the files you expect to touch, the approach and how you will check it. Then `sf ticket set <KEY> --stage planning` and add the key to this run's queue.
   - Anything else: run `/shopfloor:spec <KEY>`. If it leaves the ticket at `planning` with no open question, add it to the queue the same way.

## 7. Hand off builds
If paused, or `sf build status` shows a runner already working, hand off nothing and log each waiting key as `waiting-for-runner`. Otherwise hand the queue over in one call, each key once and only if still at `planning`, swept tickets first, at most `limits.maxParallel` keys: `sf build start <KEY> [<KEY> ...]`. The runner starts one `/shopfloor:build` session per ticket. Keys left out stay at `planning` for the next tick's sweep.

## 8. Close
`sf runs log <KEY> <result> --note "<short note>"` for each ticket you moved, results like `claimed`, `queued`, `answered`, `backed-off`, `stopped`, `waiting-for-runner`. Nothing happened: end quietly.

In dry run (`sf config` shows `dryRun`), tracker writes and PRs only report what they would do; post that in the thread and carry on.
