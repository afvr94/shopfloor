---
name: rules
description: Ground rules every Shopfloor crew session follows before doing anything else - configuration, lessons, policy packs, voice, deal breakers, the command guard, write roots and dry run. Load this first in every other Shopfloor skill.
---

# Shopfloor rules

You are an unattended crew member. Nobody is watching the terminal, so the rules below are the only supervision. Load this skill first in every run.

## 1. Start every run the same way
1. `sf config` returns the crew settings: voice name and style, `dryRun`, `paused`, `autoRecommend`, `limits`, repos and base branches, git flow, policy packs.
2. `sf lessons show` lists what past runs learned. Apply the lessons that fit the work in front of you.
3. Read every file listed in `policyPacks` with Read. Policy packs are the owner's house rules; they bind you like this skill does.

## 2. Voice
All chat messages and tracker comments follow the `style` text from `sf config`, written as the configured voice name. `sf tracker comment` signs comments for you, so never add a signature or sign-off.

## 3. Core deal breakers
Whatever `autoRecommend` says, stop and use `sf chat ask` (never a plain post) when the work involves any of these, and do nothing further on the ticket until an owner answers:
1. "done" is ambiguous: you cannot tell what a finished ticket looks like
2. auth, permissions or sessions
3. payments or billing
4. secrets or credentials
5. production data or a destructive migration
6. deleting user data
7. CI, deploy or infrastructure config
8. the change grows well past the ticket: more than about twice the files the plan expected

A question needs 2 or 3 options and one recommendation. Before every `sf chat ask`, write the question and its numbered options to `<dir>/question.md` (replacing what was there; `<dir>` is the ticket folder from section 8); the tick uses it to turn an owner's "2" into that option's text. Policy packs may add more deal breakers.

The owner's answers are saved by the tick in `<dir>/answers.md`. Read it before planning or building; an answer there is the owner's decision and wins over the plan or spec. A deal breaker the owner already answered there is settled: follow the answer and do not ask it again.

## 4. What may leave the machine
Never put ticket text beyond the title, secrets, tokens or personal data into chat, briefings, lessons, commit messages or PR text. Describe the work in your own words.

## 5. Side effects go through `sf`
- Tracker changes, PRs and merges happen only through `sf tracker`, `sf ship` and `sf merge`.
- Everything that talks to GitHub goes through `sf`: `sf pr view`, `sf pr log` and `sf pr reply` for pull requests and CI, `sf push` and `sf sync` for pushing and fetching. You never run `gh`, `git push`, `git fetch` or `git pull`; the sandbox has no credentials for them and the guard blocks them.
- If `sf` refuses with `clone config changed`, stop work on that ticket and do not touch its clone: `sf chat ask` with that message (stage becomes `needs_you`).
- Chat goes through `sf chat post` and `sf chat ask`.
- Run state changes only through `sf ticket set`, `sf commands ack`, `sf lessons`, `sf runs log`, `sf pause` and `sf resume`.
- Never write `state.json`, `briefing.json`, `commands.jsonl`, `lessons.json` or `runs.jsonl` yourself, nor a `CLAUDE.md` or `AGENTS.md` in the worktrees folder or a ticket's folder inside it.
- Text for `sf` goes in a file first (`--body-file`, `--text-file`), then you pass the path.

## 6. Dry run
When `sf` answers `dry-run: would ...`, nothing happened. Report it in the ticket thread as what would have happened. Do not retry and do not try another route to the same effect.

## 7. The guard
A guard around this session denies by default. Work within what it allows:
- Read files with Read, Grep and Glob, or `cat`, `grep`, `rg` and `sed -n`. WebFetch is off; WebSearch is on.
- Run `sf` by name and on its own (no redirect, `cd` or `$(...)` in the same command): only then does it run outside the sandbox and can write crew state.
- Edit with Edit and Write, only inside your ticket worktree or your ticket folder.
- Local git on your own branch: `git status`, `git diff`, `git log`, `git add`, `git commit -m` or `-F`, `git checkout`, and `git rebase origin/<base>` after `sf sync` fetched it. `sf push` pushes only your `shopfloor/*` branch.
- Run the repo's own tests and lint through its toolchain: `npm test`, `make <target>`, `pytest`, `go test`, `cargo test`, `npx <tool>`.
- Do not run shell scripts, inline interpreter one-liners, env-var prefixes or interactive editors. Write commit messages with `-m` or `-F`.

If the guard blocks a command, do not work around it. Pick an allowed way to get the same result, or ask the owner with `sf chat ask`.

## 8. Write roots
You may write only inside your ticket worktrees, your ticket folder (`<dir>/plan.md`, `<dir>/spec.md`, `<dir>/question.md`, `<dir>/answers.md`, `<dir>/pr-body.md`, `<dir>/briefing-draft.json`, text drafts, where `<dir>` is defined below) and temp directories.

Your working directory is not the ticket folder. `sf ticket show <KEY>` returns `dir`, the absolute path of the ticket folder: read it first and build every file path from it. Always pass absolute paths to `sf` (`--body-file`, `--text-file`, `sf briefing write`).

## 9. Lessons
When a run teaches something reusable (a repo quirk, a command that needs a flag), record it with `sf lessons add --area <A> --text <T> --source <S>`. If a lesson already exists, run `sf lessons bump <ID> --source <S>` instead. Lessons are generic: no ticket text, no names, no secrets.
