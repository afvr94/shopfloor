# Contributing

Thanks for helping. Bug reports, adapter requests and pull requests are all welcome. Security bugs go through [SECURITY.md](SECURITY.md), not a public issue.

## Running the tests

Shopfloor has no dependencies: Python 3.10+ and `git` are enough. Always run the tests with a throwaway home, so no test can touch your real `~/.shopfloor`, launchd or systemd:

```sh
HOME=$(mktemp -d) SHOPFLOOR_HOME=$(mktemp -d)/sf python3 -m unittest
```

The suite takes under a minute. CI runs it on Ubuntu and macOS with Python 3.10 and 3.13, and all four jobs must pass before a pull request can merge. CI sets `HOME` outside the temp dir on purpose: the temp dir is a crew write root, and the guard tests check that writes to `~` are blocked.

## Pull requests

- **Every change needs a test** that fails without it, unless it only touches docs.
- **Titles follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):** `feat: add a jira tracker`, `fix(guard): block git -C pushes`, `docs: clarify dry run`. Use a lowercase, imperative description with no trailing period.
- **Keep it small.** One change per pull request is easier to review and to revert.
- **Standard library only.** A new dependency needs a very good reason, so open an issue first.
- **Docs move with the code.** A new config key goes in [docs/config.md](docs/config.md), and `tests/test_docs.py` fails until it does. A change to what a crew can or can't do goes in [docs/SAFETY.md](docs/SAFETY.md).

## Changing the guard

The guard (`engine/hooks/guard.py`) decides what a crew session may run, so treat every change to it as a security change. Its tests are a table: `ROWS` in `engine/hooks/test_guard.py`. Each row is `(label, tool, tool_input, cfg_overrides, cwd_kind, blocked)`:

```python
("push explicit main", BASH, "git push origin main", {}, "wt", True),
```

Add a row for every command you allow or block, and add rows for the obvious ways around it: other flags, quoting, redirects, `env`, `sh -c`, a different working folder. The default is deny. If the guard can't parse something, it blocks it, and it should stay that way.

## Adding an adapter

Crew skills never call Linear, Slack or GitHub directly. They go through `sf`, which talks to three kinds of adapter in `engine/adapters/`:

| Kind | Factory | Example | Config key |
| --- | --- | --- | --- |
| Tracker | `make_tracker` in `tracker/__init__.py` | `LinearTracker` in `tracker/linear.py` | `tracker.type` |
| Chat | `make_chat` in `chat/__init__.py` | `SlackChat` in `chat/slack.py` | `chat.type` |
| Git flow | `make_gitflow` in `gitflow/__init__.py` | `PrOnly` in `gitflow/pr_only.py` | `gitFlow.type` |

A new adapter needs:

1. **The class**, with the same methods as the example. For a tracker those are `list_candidates`, `get`, `transition`, `comment`, `update_comment`, `link_pr`, `viewer` and `team_check`. Raise `TrackerError` or `ChatError` with a message the owner can act on. Take the HTTP call as a constructor argument (`post=` or `call=`), so tests can pass a fake.
2. **Wiring:** pick it in the factory, accept the new type in `validate` in `engine/lib/config.py`, and add its defaults to `DEFAULTS` there.
3. **Doctor rows** in `cli/doctor.py`, which check the token and the setup the adapter needs, with a fix the owner can follow.
4. **The `init` wizard** in `cli/init.py`, so a new crew can choose it.
5. **Tests** against a fake in `tests/fakes.py`. Keep real network calls out of the suite.
6. **Docs:** the config keys in `docs/config.md`, and a `docs/setup-<name>.md` guide for getting the token.

Keep the safety model as it is: only `sf` holds the token, and crews never get it. If the adapter needs a new credential, it goes in a file under `~/.shopfloor/secrets`, and the guard and the sandbox must both refuse it to crews. Open an issue before you start on a big one, so we can agree on the design first.

## Code style

- Follow the code around you: plain functions, small modules, type hints, `from __future__ import annotations`.
- Comment only what the code can't say: a non-obvious reason, a gotcha, a limit.
- Errors name the fix. A crew owner should know what to do from the message alone.
