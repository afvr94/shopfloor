# GitHub chat setup

A crew can talk to you in the comments of the ticket's GitHub issue instead of a Slack thread. It needs no app and no token file. It works only with the GitHub tracker, because the thread is the ticket's issue.

## 1. Set the chat

```json
"chat": {"type": "github", "github": {"owner": "your-login"}}
```

`shopfloor init` asks "Chat (slack/github)" when you pick the GitHub tracker, and fills `owner` from `gh api user`. `chat.tokenFile` is refused for GitHub chat, and `shopfloor secret <crew> chat` says so.

## 2. Who posts

The crew posts with your `gh` login, the same one it uses to read issues and open pull requests. Its comments show under your own name, signed with the crew's voice name. Every comment Shopfloor writes ends with a hidden line, `<!-- shopfloor -->`. That line is how it tells its comments from yours.

Dry run doesn't hold chat back. A crew in dry run still posts its plan and questions as comments on the issue. On a public repo, anyone can read them.

`sf` posts only to the ticket's own issue in the configured repo. A thread recorded for another repo, such as a Slack thread left over from before you switched chat, is refused; cancel the ticket or start it over.

## 3. Who is heard

Only comments from `chat.github.owner` count as replies. Comments from anyone else are ignored, collaborators included. Comments written before the crew picked the issue up are ignored too, and so are edits. To change a reply, write a new comment.

The board's listener checks issue comments about once a minute.

## 4. Replying

Replies work as they do in Slack.

- Answer an open question with its number, or in your own words.
- `approve` accepts the plan.
- `adjust <what>` asks for a change.
- `retry` or `go` runs a stopped ticket again.
- `pause`, `resume` and `cancel` control the ticket.
- `lesson <text>` saves a lesson for the crew, and `forget <id>` removes one.

## 5. Check it

```sh
shopfloor doctor
```

Two rows matter: `<crew>: github auth` and `<crew>: github channel`.

## Limit

GitHub does not notify you about your own comments, so crew questions will not ping you. Watch the board for tickets that need you. A separate bot account is planned.
