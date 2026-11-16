---
name: spec
description: Turns a Shopfloor ticket that is not clear and small into a short written spec, asks the owner the open questions, and queues the ticket once they are settled. Use for "/shopfloor:spec <KEY>".
---

# Shopfloor spec

Load `/shopfloor:rules` first. Input is a ticket key.

## 1. Understand
1. `sf ticket show <KEY>` gives the title, stage, thread and `dir`, the absolute ticket folder; build every file path below from it. `sf tracker get <KEY>` gives the ticket itself.
2. Read the repo's docs and the code the ticket touches with Read, Grep and Glob. Find the real files, not guesses.
3. Check the deal breakers in `/shopfloor:rules`. If one applies, `sf chat ask` with 2 or 3 options and a recommendation, then stop. This ignores `autoRecommend`.

## 2. Write the spec
Write `<dir>/spec.md` with these sections, short and concrete:
- **Goal**: what is true when this is done, in the owner's terms.
- **Approach**: how, in a few steps.
- **Files**: the files you expect to change, per repo. Build uses this count for the "grows past the ticket" check.
- **Open questions**: anything you cannot decide alone, each with 2 or 3 options and your recommendation. Write "none" if there are none.

Set the stage with `sf ticket set <KEY> --stage planning`. Post a summary of the spec in the thread with `sf chat post <KEY> --text-file <dir>/<file>`.

## 3. Open questions
No open questions: queue the ticket (`sf ticket set <KEY> --stage planning`; the tick adds it to its build queue) and stop.

With open questions, `autoRecommend` from `sf config` decides:
- `questions`: take your recommendation for each non-deal-breaker question, update `spec.md` to say so, and post the choice with `sf chat post <KEY> --text-file <dir>/<file>`: the question, what you chose and why. Do not use `sf chat ask`, it would mark the ticket as waiting on the owner. Then queue the ticket.
- `never`: ask each question with `sf chat ask <KEY> --question "<text>" --option "<a>" --option "<b>" --recommend <N>`. The ticket moves to `needs_you`. Stop; a later tick resumes it when the owner answers.

Deal breakers are always asked, in both modes, and always stop the ticket.

## 4. When the owner answers
The tick returns the ticket to this skill with the answer. Fold it into `spec.md`, post a short confirmation in the thread, and queue the ticket.

In dry run, say in the thread what `sf` reported it would have done.
