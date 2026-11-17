# Changelog

All notable changes to Shopfloor are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-11-17

### Added

- GitHub issue comments as the crew's chat: with the GitHub tracker, a crew needs only a GitHub account. See [docs/setup-github-chat.md](docs/setup-github-chat.md).
- The crew replies in the thread when it pauses or resumes.

### Changed

- `sf` only posts to the ticket's own issue, and crews can no longer set a ticket's thread.
- The GitHub tracker only edits comments Shopfloor wrote.
- Slack crews must set `chat.tokenFile`; a config without it now fails validation instead of failing on first use.
- CI runs on Ubuntu only. Run the suite on a Mac before relying on macOS behaviour.

### Fixed

- `shopfloor install` no longer fails with `Bootstrap failed: 5` when the board is already running.

## [0.2.1] - 2026-11-17

### Fixed

- A ticket first built in dry run and then retried now moves from todo through in progress to review, instead of failing the move to review.
- Config validation rejects a Slack channel ID pasted as `chat.slack.ownerUserId`; the crew ignored the owner's thread replies.
- A GitHub crew's pull request description ends with `Closes <owner>/<repo>#<n>`, so the issue closes when the pull request merges.

## [0.2.0] - 2026-11-16

### Added

- A GitHub Issues tracker: label an issue `shopfloor` and a crew takes it, using your gh login. Set it with `tracker.type: "github"`; see [docs/setup-github-issues.md](docs/setup-github-issues.md).
- `shopfloor init` asks which tracker to use, and for GitHub offers to create the `shopfloor`, `shopfloor:in-progress` and `shopfloor:review` labels.
- `shopfloor doctor` checks a GitHub crew's issue repo and labels.

### Changed

- A Linear crew's config without `tracker.tokenFile` now fails validation with a clear message, instead of failing on first use.
- `sf tracker get` and `sf tracker candidates` include a `closed` field. A closed GitHub issue makes the crew stand down unless its pull request is already open.
- CI uses `actions/checkout` v7 and `actions/setup-python` v7.

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

[Unreleased]: https://github.com/afvr94/shopfloor/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/afvr94/shopfloor/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/afvr94/shopfloor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/afvr94/shopfloor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/afvr94/shopfloor/releases/tag/v0.1.0
