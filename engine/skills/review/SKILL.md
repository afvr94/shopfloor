---
name: review
description: Reviews a Shopfloor ticket's branch before it ships - simplify, TypeScript and frontend passes when those files changed, then correctness last - fixing what each pass accepts and committing each pass on its own. Use for "/shopfloor:review <KEY>"; the build skill runs it before shipping.
---

# Shopfloor review

Load `/shopfloor:rules` first. Input is a ticket key whose branch is committed and rebased in its worktree. Each pass reviews the diff and fixes what it accepts. Simplify runs first so later passes read less code; correctness runs last so it sees the final diff, including anything an earlier fix broke.

## 1. Scope
1. `sf ticket show <KEY>` for the repo, `branch` and `dir`; `sf config` for the repo's base branch.
2. In the worktree, `git diff --name-only origin/<base>...HEAD` lists the changed files. If it is empty, write `<dir>/review.md` saying there was nothing to review, and stop.
3. Note the repo's own test and lint commands, the ones you ran while building.

## 2. Passes
Run them in this order, one at a time, because each pass edits the tree the next one reads. Skip a pass when its condition fails and note why.

| # | Pass | Runs when | Looks for |
|---|------|-----------|-----------|
| 1 | Simplify | always | code nobody asked for (options, wrappers, abstractions, "for later" code), duplicates of a helper the repo already has, dead code, comments that restate the code or name a ticket, unclear names |
| 2 | TypeScript | a `.ts` or `.tsx` file changed | `any`, casts and non-null assertions that hide a real type, external data used without parsing at the boundary, a `switch` over a union that misses a case |
| 3 | Frontend | a UI file changed (components, screens, styles, markup) | missing labels, keyboard access or focus states, no loading, empty or error state, new styles where the repo already has a component or token |
| 4 | Correctness | always | bugs, unhandled edge cases and errors, broken callers of a changed function, security holes, tests that do not test the change |

Run each pass in a fresh Agent, in the foreground. The brief is all the context it gets, so it holds:
- the worktree path, `origin/<base>...HEAD` as the diff to review, and the changed-file list
- the pass's row from the table above, as the only thing to look for
- "Read the code around the diff, not only the diff. Fix the findings you accept in the working tree. Do not commit, and do not touch files outside the worktree."
- "Follow section 7 of `/shopfloor:rules` and these policy packs", with the `policyPacks` paths from `sf config`. "If a finding hits a deal breaker in `/shopfloor:rules`, report it, do not fix it, and do not ask the owner yourself."
- one line per fix earlier passes made, so this pass does not undo one without saying why. Never their opinions or "nothing found" verdicts.
- "Reply with each finding marked fixed, skipped with the reason, or no change needed."

Do not put your own view of the code in the brief; an agent handed a conclusion tends to confirm it.

A finding that hits a deal breaker in `/shopfloor:rules` is not fixed in the pass: stop and `sf chat ask`, and leave the worktree as it is.

## 3. After each pass that changed files
1. Run the repo's tests and lint. If they fail, fix the failure, or `git restore` that pass's edits and `rm` the files it added. Never carry a red tree into the next pass.
2. `git add` the pass's files and commit them on their own with `git commit -m`, a Conventional Commit with the ticket key as scope, for example `refactor(<KEY>): simplify per review`. No attribution or sign-off lines.

## 4. Record
Run the repo's full tests and lint once more. Write `<dir>/review.md`: one line per pass with its outcome (`fixed`, `no change needed` or `skipped` and why), then the findings skipped on purpose and why. The build skill uses it for the PR body and the briefing's risk.
