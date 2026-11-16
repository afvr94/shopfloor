# Linear setup

Shopfloor reads tickets and writes comments and state changes through Linear's GraphQL API with a personal API key.

## 1. Create an API key

In Linear, open Settings, then Account, then Security & access, and create a personal API key. Store it in a file that only you can read:

```sh
mkdir -p ~/.shopfloor/secrets && chmod 700 ~/.shopfloor/secrets
printf '%s\n' 'lin_api_...' > ~/.shopfloor/secrets/linear
chmod 600 ~/.shopfloor/secrets/linear
```

`shopfloor init` does this for you when it prompts for the key. The key is never printed, logged or put in a command line. A secret file that is not mode `0600` is refused.

## 2. Create the opt-in label

Create a label named `shopfloor` on the team (or workspace). A crew only touches tickets that carry it. The name is configurable in `tracker.linear.label`; matching ignores case.

To keep a labelled ticket away from the crew, add a hold label (default `hold`, set in `tracker.linear.holdLabels`). A ticket with a hold label is never picked up and never changed.

## 3. Match state names

`tracker.linear.states` maps three crew stages to your workflow's state names, which must match exactly:

| Key | Default in the wizard | Meaning |
| --- | --- | --- |
| `todo` | `Todo` | Where new work waits. Only tickets in this state are claimed. |
| `inProgress` | `In Progress` | The crew moved the ticket here when it started. |
| `review` | `In Review` | A pull request is open. |

`tracker.mayMove` lists the moves a crew may make, as `from->to` pairs of those keys (the wizard sets `todo->inProgress` and `inProgress->review`). Any other move is refused. `shopfloor doctor` checks that the team, the label and every state name exist.

## 4. Whose tickets

`tracker.linear.assignee` limits which tickets a crew takes:

- `null` (default): any assignee
- `"me"`: only tickets assigned to the owner of the API key
- an email address: only tickets assigned to that person

## 5. Running on your own key

Every comment a crew writes is signed with the crew's voice name (`voice.name`), for example "Shopfloor, a Shopfloor crew". Comments are therefore distinguishable from yours even though they are posted with your key, so a crew can run on your own account.
