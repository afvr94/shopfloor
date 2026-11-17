# Changelog

All notable changes to Shopfloor are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- A GitHub Issues tracker: label an issue `shopfloor` and a crew takes it, using your gh login.

## [0.1.0] - 2026-11-16

The first public release.

### Added

- Crews of Claude Code sessions that pick up Linear tickets labeled `shopfloor`, plan them in a Slack thread, build them in a sandboxed clone, open a pull request with a briefing page, and tend it until it lands.
- Three git flows: `pr-only` (the default), `squash-on-green` and `merge-to-test-branch`.
- Thread commands: replies in a ticket's Slack thread answer the crew's questions, approve or adjust its plan, retry, pause, resume or cancel a ticket, and teach or take back lessons.
- `sf`, the only command that talks to GitHub, Linear and Slack for a crew. It pushes only the ticket's own `shopfloor/` branch.
- The guard, a default-deny `PreToolUse` hook on every crew tool call.
- Signed, tamper-evident state in folders crews can't write.
- Dry run by default for every new crew.
- A local board on 127.0.0.1 with each crew's tickets, runs and briefings.
- `shopfloor init`, `doctor` (with `--live` and `--repair-tamper`), `secret`, `install`, `uninstall`, `run`, `status` and `board`.
- Schedules through launchd on macOS and systemd on Linux.

[Unreleased]: https://github.com/afvr94/shopfloor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/afvr94/shopfloor/releases/tag/v0.1.0
