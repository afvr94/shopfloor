# Security policy

Shopfloor runs Claude Code unattended on your machine with your `gh` and Claude logins, so a security bug here can cost you real damage. Please report it privately.

## How to report

Use GitHub's private vulnerability reporting: open the [Security tab](https://github.com/afvr94/shopfloor/security) and click **Report a vulnerability**. Please don't open a public issue, pull request or discussion for it.

Include the Shopfloor commit, your OS, your Claude Code version (`claude --version`), and the smallest steps that reproduce it. If the bug is in the guard, the exact command or tool input that got through is the most useful thing you can send.

I aim to reply within a week, and to agree on a fix and a disclosure date with you before anything is made public. I'll credit you in the advisory unless you'd rather not be named.

## In scope

- **Credential exposure:** a crew session that can read or send a token, SSH key, cloud credential or `~/.shopfloor/secrets`, or a log, board page, briefing or Slack message that leaks one.
- **Sandbox or guard escape:** a crew that runs a command the guard should block, writes outside its worktrees and tickets folders, reaches the board or another local service, or loads your own Claude settings, hooks, plugins or MCP servers.
- **`sf` bypass:** a crew that pushes a branch other than its ticket's own `shopfloor/` branch, merges outside its git flow, acts while dry run or pause is on, or changes a signed record without `sf` flagging it.
- **The board and the Slack listener:** a way for anything other than you to read a briefing or queue a command.

[docs/SAFETY.md](docs/SAFETY.md) explains each layer and lists the limits it already knows about. A report about one of those limits is still welcome if you can show it does more harm than the page says.

## Out of scope

- What a crew writes in a pull request it was allowed to open. Reviewing that code is what the pull request and its briefing are for.
- Attacks that need you to edit your crew config or policy packs against your own interest.
- Bugs in Claude Code, `gh`, git or the OS sandbox themselves. Report those upstream. If Shopfloor relies on the broken behavior, tell me as well.

## Supported versions

Only the latest release and `main` get security fixes.
