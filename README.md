# Shopfloor

Shopfloor turns tickets into pull requests. A crew watches a Linear team for tickets carrying an opt-in label, plans each one in a Slack thread, builds it in its own clone of the repo with Claude Code, opens a pull request, and tends it until you merge it. You stay in the loop through the Slack thread and a local board.

It is plain Python (standard library only) plus Claude Code skills. A crew wakes on a schedule (launchd on macOS, systemd on Linux), runs one tick, and exits.

Read [docs/SAFETY.md](docs/SAFETY.md) before you turn dry run off. Crews run unattended with your credentials.

## Requirements

- macOS or Linux
- [Claude Code](https://docs.claude.com/en/docs/claude-code), logged in
- `git`
- `gh`, logged in (`gh auth login`)
- Python 3.9 or newer
- On Linux, Claude Code's sandbox needs `bubblewrap` (`bwrap`) and `socat`. On macOS it uses `sandbox-exec`, which is built in.

`shopfloor doctor` checks all of these, plus your Linear and Slack setup and GitHub branch protection.

Every base branch must be protected. GitHub's free plan only protects branches on public repos, so a private repo needs GitHub Pro, Team or Enterprise.

## Quick start

1. Get the code and run the setup wizard. It asks for a crew id, Linear team, Slack channel and your repo, stores your tokens in `0400` files under `~/.shopfloor/secrets` (a `0500` folder), writes the crew config and installs the schedule.

   ```sh
   git clone https://github.com/afvr94/shopfloor
   cd shopfloor
   bin/shopfloor init
   ```

   Have these ready: a Linear API key ([docs/setup-linear.md](docs/setup-linear.md)) and a Slack bot token, your member ID and a channel ID ([docs/setup-slack.md](docs/setup-slack.md)).

2. Check everything.

   ```sh
   bin/shopfloor doctor
   ```

   Fix every failing row. A protected branch without GitHub branch protection fails the check on purpose.

3. In Linear, add the `shopfloor` label to a ticket in your "new work" state.
4. Wait for the next scheduled run, or start one now with `bin/shopfloor run <crew-id>`.
5. Watch the ticket's Slack thread. The crew posts its plan, asks questions with a recommended answer, and reports progress. Reply in the thread to answer.

The board is at `http://127.0.0.1:7420` once installed (`bin/shopfloor board` runs it in the foreground).

## Dry run

Every new crew starts with `"dryRun": true`. In dry run the crew chats and builds locally, but it does not push, open pull requests, write to Linear or merge. `sf` prints `dry-run: would ...` for each skipped action. When the output looks right, set `"dryRun": false` in `~/.shopfloor/crews/<crew-id>.json`. The wizard never does that for you. Tickets a dry run already took stay where the dry run left them, with no pull request; reply `retry` in a ticket's thread to build it again for real, or `cancel` to drop it.

## Commands

| Command | What it does |
| --- | --- |
| `shopfloor init` | Setup wizard for one crew. |
| `shopfloor doctor [crew]` | Check tools, sandbox support, secrets, Linear, Slack, repos and branch protection. |
| `shopfloor doctor --live [crew]` | Also start one real sandboxed crew session in a temp folder (uses your Claude plan). |
| `shopfloor doctor --repair-tamper <crew>` | Clear a crew's tamper markers once you have checked the records they name (see [SAFETY.md](docs/SAFETY.md) section 4). |
| `shopfloor secret <crew> tracker\|chat` | Replace a crew's Linear or Slack token (asks for it hidden). |
| `shopfloor install [crew]` | Install the board service and each crew's schedule. |
| `shopfloor uninstall` | Remove the installed services. |
| `shopfloor run <crew>` | Run one tick now. |
| `shopfloor status [crew]` | One line per crew. |
| `shopfloor board` | Serve the board in the foreground. |

## Configuration

See [docs/config.md](docs/config.md) for every key. Examples live in [examples/](examples/): [crew.example.json](examples/crew.example.json), [policy-pack.example.md](examples/policy-pack.example.md) and [slack-app-manifest.json](examples/slack-app-manifest.json).

## Optional plugins

Nothing beyond the requirements is needed. If you already use Claude Code plugins for code review or test-driven development, they work alongside a crew, but a crew never depends on them.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
