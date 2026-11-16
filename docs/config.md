# Configuration reference

Everything lives under `~/.shopfloor` (override with the `SHOPFLOOR_HOME` environment variable). A crew's config is `~/.shopfloor/crews/<id>.json`; the board's is `~/.shopfloor/board.json`. Missing keys take the defaults below, and nested objects are merged key by key. A full example is [examples/crew.example.json](../examples/crew.example.json). `shopfloor doctor` validates the file.

Paths beginning with `~` are expanded.

## Crew

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `id` | string | required | Crew id; lowercase letters, digits and `-`, up to 31 characters. Must match the file name. |
| `name` | string | the id | Display name on the board. |
| `dryRun` | boolean | `true` | When true, no push, pull request, tracker write or merge happens. See [SAFETY.md](SAFETY.md). |
| `paused` | boolean | `false` | When true, `sf merge` refuses to merge (enforced in code). Not starting builds or claiming new tickets is crew-instruction policy, not a technical lock. The crew still plans claimed tickets and tends open pull requests. `pause` and `resume` owner commands toggle it. |
| `autoRecommend` | string | `"questions"` | `"questions"` lets the crew proceed on its own recommendation for ordinary questions; `"never"` always waits for you. Deal breakers (such as schema changes or dependency changes) always wait. |
| `protectedBranches` | string[] | `["main", "master"]` | Branches no crew may push to or merge into directly. Each needs GitHub branch protection. Repo base branches are protected too. |
| `policyPacks` | string[] | `[]` | Paths to Markdown files of house rules the crew reads at the start of every run. See [policy-pack.example.md](../examples/policy-pack.example.md). |

### `voice`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `voice` | object | see below | How the crew speaks. |
| `voice.name` | string | `"Shopfloor"` | Name shown on Slack messages and used to sign Linear comments. |
| `voice.style` | string | `"plain"` | `"plain"` means plain, neutral, brief. Any other string is used as the style instruction as written. |

### `limits`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `limits` | object | see below | Positive integers only. |
| `limits.maxParallel` | integer | `2` | Builds that run at the same time. |
| `limits.maxFixRounds` | integer | `3` | Rounds of fixing review comments and failing checks per pull request before the crew asks you. |
| `limits.maxNewPerTick` | integer | `2` | New tickets claimed in one run. |
| `limits.buildMaxMinutes` | integer | `180` | A build session is stopped after this long. |
| `limits.tickMaxMinutes` | integer | `60` | A tick session is stopped after this long. |
| `limits.bundleMaxMB` | integer | `200` | Largest bundle (MiB) `sf push` and `sf ship` take from a ticket folder. A bigger one is refused before it is copied. |

### `schedule`

Required for `shopfloor install` to install the crew's timer.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `schedule` | object | none | When ticks run. |
| `schedule.start` | string `HH:MM` | required | First run of the day, local time. |
| `schedule.everyHours` | integer 1 to 24 | required | Hours between runs. |
| `schedule.until` | string `HH:MM` | required | Last possible run; must be later than `start`. |
| `schedule.weekdaysOnly` | boolean | `false` | Skip Saturday and Sunday. |

### `repos`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `repos` | object | required | One entry per repo, keyed by a short name you choose. At least one. |
| `repos.<name>` | object | | A repo the crew may work in. |
| `repos.<name>.github` | string | required | `owner/name` on GitHub. `doctor` checks that the checkout's `origin` matches. |
| `repos.<name>.base` | string | required | Branch pull requests target. |
| `repos.<name>.path` | string | required | Local checkout. Crews work in their own full clone of it per ticket, never in this directory; it is only read and fetched. |

### `tracker`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `tracker` | object | required | Ticket source. |
| `tracker.type` | string | required | Must be `"linear"`. |
| `tracker.tokenFile` | string | required | Path to the `0600` file holding the Linear API key. |
| `tracker.mayMove` | string[] | none | Allowed state moves as `from->to`, both keys of `tracker.linear.states`. |
| `tracker.linear` | object | | Linear settings. |
| `tracker.linear.team` | string | required | Team key, such as `ENG`. |
| `tracker.linear.label` | string | `"shopfloor"` | Opt-in label (case-insensitive). |
| `tracker.linear.assignee` | string or null | `null` | `null` for anyone, `"me"` for the API key's owner, or an email address. |
| `tracker.linear.holdLabels` | string[] | `["hold"]` | A ticket with any of these labels is left alone. |
| `tracker.linear.states` | object | required | Maps crew stages to workflow state names. |
| `tracker.linear.states.todo` | string | required | State where new work waits. |
| `tracker.linear.states.inProgress` | string | required | State for work in progress. |
| `tracker.linear.states.review` | string | required | State once a pull request is open. |

### `chat`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `chat` | object | required | Where the crew talks to you. |
| `chat.type` | string | required | Must be `"slack"`. |
| `chat.tokenFile` | string | required | Path to the `0600` file holding the bot token. |
| `chat.slack` | object | | Slack settings. |
| `chat.slack.channel` | string | required | Channel ID where ticket threads are opened. |
| `chat.slack.ownerUserId` | string | required | Member ID of the only user whose replies count. |

### `gitFlow`

What a crew may do with a pull request once it is open. See [SAFETY.md](SAFETY.md) for the exact rules.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `gitFlow` | object | | Merge behaviour. |
| `gitFlow.type` | string | `"pr-only"` | `"pr-only"` never merges. `"squash-on-green"` squash-merges a green pull request. `"merge-to-test-branch"` merges green pull requests into `testBranch` and leaves them open. |
| `gitFlow.testBranch` | string or null | `null` | Required for `merge-to-test-branch`. Must not be a protected branch or a repo base branch. |
| `gitFlow.mergeDenyPaths` | string[] | `[]` | Glob patterns; a pull request touching a matching file is never merged by a crew. |

### `guard`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `guard` | object | | Extra limits for the crew's tool guard. |
| `guard.offLimits` | string[] | `[]` | Paths the crew may not read or write (treated like secrets). |
| `guard.secretFiles` | string[] | credential defaults | Files treated like secrets: never read or written by the crew, in the guard and in the sandbox's deny-read list. The defaults are `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `~/.docker/config.json`, `~/.kube`, `~/.gnupg`, `~/.claude/.credentials.json` and `~/.claude.json`; entries you list add to them. |

## Board

`~/.shopfloor/board.json` is optional.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `board.host` | string | `"127.0.0.1"` | Address the board listens on. Keep it on loopback; see [SAFETY.md](SAFETY.md). |
| `board.port` | integer | `7420` | Port the board listens on. |
| `board.crews` | string[] | `[]` | Crew ids the board and `install` manage. When empty, every file in `crews/` is used. `init` adds the crew it creates. |
| `board.labelPrefix` | string | `"dev.shopfloor"` | Prefix for launchd labels (macOS); systemd units are named `shopfloor-*`. |
| `board.pollSeconds` | integer | `60` | Seconds between checks for your replies in Slack. |
| `board.linkBase` | string or null | `null` | Base URL used in links to the board, for example when you reach it through a tunnel. Defaults to the local address. |
