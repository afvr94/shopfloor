# Slack setup

A crew opens one thread per ticket in a channel and reads your replies in it. Replies are read by polling every 60 seconds, so the app needs no events, no socket mode and no public URL.

## 1. Create the app

1. Go to <https://api.slack.com/apps> and choose Create New App, then From a manifest.
2. Pick your workspace and paste the contents of [examples/slack-app-manifest.json](../examples/slack-app-manifest.json).
3. Create the app.

The manifest requests exactly these bot scopes and nothing else:

| Scope | Used for |
| --- | --- |
| `chat:write` | posting thread messages |
| `chat:write.customize` | posting under the crew's voice name |
| `channels:history` | reading replies in public channels |
| `groups:history` | reading replies in private channels |
| `channels:read` | checking that the bot is in a public channel |
| `groups:read` | checking that the bot is in a private channel |

It enables no event subscriptions, no interactivity, no socket mode and no request URLs.

## 2. Install and copy the token

Choose Install to Workspace, then copy the Bot User OAuth Token (it starts with `xoxb-`). `shopfloor init` asks for it and stores it in a `0400` file under `~/.shopfloor/secrets`.

## 3. Invite the bot

In the channel you want to use, run `/invite @Shopfloor` (use the bot's display name). `shopfloor doctor` fails if the bot is not a member.

## 4. Find the IDs

- Channel ID: open the channel, click its name, and copy the ID at the bottom of the About tab (it starts with `C` or `G`).
- Your member ID: open your profile, choose More, then Copy member ID (it starts with `U`).

These go in `chat.slack.channel` and `chat.slack.ownerUserId`. The crew only acts on replies from that one user; messages from anyone else in the thread are ignored.
