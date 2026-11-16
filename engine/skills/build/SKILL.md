---
name: build
description: Builds one Shopfloor ticket from its plan or spec in an isolated worktree, runs the repo's own checks, commits, opens the pull request, writes the briefing, and moves the ticket to review. Use for "/shopfloor:build <KEY>".
---

# Shopfloor build

Load `/shopfloor:rules` first. Input is a ticket key. Work only in the ticket's worktree.

## 1. Prepare
1. `sf ticket show <KEY>` for the stage, repo and any existing branch. Read `tickets/<KEY>/plan.md` or `spec.md`.
2. `sf worktree prepare <KEY> <repo>` prints the worktree path. A rebuild reuses leftover work: check `git status` and the existing commits first, and finish what is there.
3. `sf ticket set <KEY> --stage building`.

## 2. Implement
1. Follow the plan or spec. Read the surrounding code and match its style and tests.
2. Write or update tests for the behavior you change.
3. Run the repo's own tests and lint with its toolchain (`npm test`, `make <target>`, `pytest`, `go test`, `cargo test`, `npx <tool>`). Fix what you break. Do not edit the checks to make them pass.
4. Keep checking the deal breakers in `/shopfloor:rules` as you learn more. If one shows up, or the change touches more than about twice the files the plan expected, stop and `sf chat ask`. Leave the worktree as it is.

## 3. Commit
Small commits on your branch with Conventional Commit messages that carry the ticket key as scope, for example `feat(ENG-12): add rate limit`. Use `git commit -m` or `-F`. No attribution or sign-off lines of any kind. Then `git fetch` and `git rebase origin/<base>`.

## 4. Ship
1. Write the PR body to `tickets/<KEY>/pr-body.md`: what changed and why, how it was checked. No ticket text beyond the title, no secrets.
2. `sf ship <KEY> <repo> --title "<type>(<KEY>): <summary>" --body-file tickets/<KEY>/pr-body.md`. It pushes your `shopfloor/*` branch and opens the PR. Never push or open a PR any other way.

## 5. Briefing
The owner did not write this code and needs to understand it fast. Write `tickets/<KEY>/briefing.json` from the final diff and the code around it:
- `title`, `whatWhy` (what changed and why, the root cause if it was a bug), `risk`
- `how`: steps of how the changed path works now
- `verify`: how to check it
- `files`: the files changed
- `prs`: objects with `repo`, `number`, `url`
- `diagram`: a Mermaid diagram of the changed path
- `size`: `small` or `full`

Title only from the ticket, no secrets, no personal data. Never put the briefing in the PR description. Then `sf briefing write <KEY> tickets/<KEY>/briefing.json` and `sf briefing url <KEY>`.

## 6. Hand over
1. `sf tracker transition <KEY> review`
2. `sf tracker link-pr <KEY> <pr-url>`
3. `sf chat post <KEY> --text-file <file>` with the PR link and the briefing link.
4. `sf ticket set <KEY> --stage in_review`

## Dry run
`sf ship` and the tracker commands print `dry-run: would ...` and change nothing. Still build, check and commit in the worktree. Post the diff stat and every "would" line in the thread, set the stage to `in_review` with no PR, and stop. Do not retry.
