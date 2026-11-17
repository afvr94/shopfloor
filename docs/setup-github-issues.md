# GitHub Issues setup

Shopfloor reads issues and writes comments and labels through `gh api`. It uses the same `gh` login that `sf` uses to push branches and open pull requests, so there is no token file to create.

Slack is still required. Crews talk to you in a Slack thread, whichever tracker they read from. See [setup-slack.md](setup-slack.md).

## 1. Log in with gh

```sh
gh auth login
gh auth status
```

Log in as the account that should own the crew's comments and labels. That is also the account that pushes and opens pull requests. It needs write access to the issue repo and to the code repo. `tracker.tokenFile` is refused for a GitHub crew.

## 2. Create the labels

A crew uses three labels on the issue repo:

| Label | Default | Meaning |
| --- | --- | --- |
| Opt-in | `shopfloor` | A crew only touches issues that carry it. |
| In progress | `shopfloor:in-progress` | The crew started the issue. |
| Review | `shopfloor:review` | A pull request is open. |

`shopfloor init` offers to create them. To do it by hand:

```sh
gh label create shopfloor --repo acme/app --force
gh label create shopfloor:in-progress --repo acme/app --force
gh label create shopfloor:review --repo acme/app --force
```

The names are configurable in `tracker.github.label` and `tracker.github.states`. `shopfloor doctor` checks that the repo is reachable, that issues are on and that all three labels exist.

## 3. Queue an issue

Add the `shopfloor` label to an open issue. The next run picks it up if it has no `shopfloor:` state label and no hold label. Pull requests and closed issues are ignored.

To keep a labeled issue away from the crew, add a hold label (default `hold`, set in `tracker.github.holdLabels`). Such an issue is never picked up and never changed.

## 4. What the crew does to labels

| Stage | Where the issue is |
| --- | --- |
| `todo` | Open, has `shopfloor`, has no state label. |
| `inProgress` | Has `shopfloor:in-progress`. The crew added it when it started. |
| `review` | Has `shopfloor:review`. A pull request is open. |

A move adds the new label first, then removes the old one. `tracker.mayMove` lists the moves a crew may make, as `from->to` pairs of `todo`, `inProgress` and `review`. Any other move is refused.

When the crew opens a pull request it comments `Pull request: <url>` on the issue, once.

## 5. Whose issues

`tracker.github.assignee` limits which issues a crew takes:

- `null` (default): any assignee
- `"me"`: only issues assigned to your `gh` login
- a GitHub login: only issues assigned to that person

## 6. Ticket keys

Issue 42 becomes the ticket `GH-42`, and its branch is `shopfloor/gh-42-<slug>`. Two crews that track different repos can both have a `GH-42`. Don't point both at the same code repo, or their branches can collide.
