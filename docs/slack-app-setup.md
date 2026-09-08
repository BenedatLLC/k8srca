# Setting up the Slack app

How to create and configure the Slack app k8srca connects to. Takes about ten
minutes. You need permission to install apps in the target workspace.

k8srca uses **plain Slack messaging** — `@`-mentions, DMs, and threads. It does
**not** use Slack's Agents / AI-apps feature. That feature is a UI surface we
don't need (suggested prompts, thread titles, Slack's own session lifecycle),
and it is mid-migration from `assistant_thread_started` to `agent_session_*`.
See [design 001 §7.1](../designs/001-architecture.md).

The app runs in **Socket Mode**: it opens an outbound WebSocket to Slack, so no
public URL, no ingress, and no tunnel. This matches the rest of the deployment,
where nothing accepts inbound connections.

---

## 1. Create the app

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
2. Name it (e.g. `k8srca`) and pick the workspace.
3. Choose the **basic app**. Do *not* enable the **Agents** / **AI Apps**
   feature — see above.

## 2. Enable Socket Mode

**Settings → Socket Mode** → toggle **Enable Socket Mode** on.

Slack will prompt you to create an app-level token as part of this. If it
doesn't, create one at **Basic Information → App-Level Tokens → Generate Token
and Scopes**:

- Name: `socket-mode`
- Scope: **`connections:write`** — the only one needed

Copy the `xapp-1-…` value. **This is `SLACK_SOCKET_MODE_TOKEN`.** It is shown
in full only once; regenerate it if you lose it.

## 3. Add bot token scopes

**OAuth & Permissions → Scopes → Bot Token Scopes.**

Required:

| Scope | Why |
| --- | --- |
| `app_mentions:read` | Receive `@k8srca` |
| `chat:write` | Post replies, and edit them via `chat.update` |
| `channels:history` | Read follow-ups in threads in public channels |
| `im:history` | Direct messages |

Optional but used:

| Scope | Why | Without it |
| --- | --- | --- |
| `reactions:write` | 👀 ack on the message being worked on | No immediate acknowledgement |
| `files:write` | Long evidence posted as a thread snippet | Long output is chunked inline |
| `users:read` | Attribute user statements in the investigation record ([002 §5.3](../designs/002-investigation-model.md)) | Statements recorded by user ID only |
| `groups:history` | Private channels | Public channels and DMs only |
| `channels:read` | Lets `k8srca slack check` verify channel membership directly | The check infers membership from a test post instead |

> **Any scope change requires reinstalling the app.** A bot token only carries
> the scopes present when it was issued. Finish the list before installing.

## 4. Subscribe to events

**Event Subscriptions → Enable Events** → on. With Socket Mode enabled there is
**no Request URL to fill in** — if the page is asking for one, Socket Mode
didn't take; go back to step 2.

Under **Subscribe to bot events**:

- `app_mention`
- `message.channels`
- `message.im`
- `message.groups` — only if you added `groups:history`

### About `message.channels`

This delivers **every message in every channel the bot has joined**, not just
mentions. That is what lets you `@`-mention once and then keep talking in the
thread; the orchestrator discards anything whose `thread_ts` isn't in its
session map.

If workspace policy forbids an app ingesting general channel traffic, omit
`message.channels` and `channels:history`. Everything still works — users must
`@`-mention on every turn instead of only the first.

## 5. Install

**OAuth & Permissions → Install to Workspace** → review → **Allow**.

Copy the **Bot User OAuth Token** (`xoxb-…`). **This is
`SLACK_BOT_USER_OAUTH_TOKEN`.**

## 6. Invite the bot to a channel

In Slack:

```
/invite @k8srca
```

**Scopes do not grant channel access.** An uninvited bot receives no events
from a channel no matter how its permissions are set. This is the single most
common reason a correctly configured app appears dead.

## 7. Configure k8srca

```bash
cp .env.example .env
```

Fill in:

```bash
SLACK_SOCKET_MODE_TOKEN=xapp-1-...      # step 2
SLACK_BOT_USER_OAUTH_TOKEN=xoxb-...     # step 5
SLACK_ALLOWED_CHANNELS=C0123ABCD        # step 6; see below
```

`.env` is gitignored. Never commit real values. Environment variables always
win over the file, so you can override one temporarily by exporting it.

**`SLACK_ALLOWED_CHANNELS`** is optional — every inbound event carries its own
channel, so the bot works in any channel it's been invited to. But an empty
allowlist means it answers *anywhere* it has been added, which during
development is exactly when it is most likely to say something wrong in front
of people. Start with one channel; widen when you trust it.

To find a channel ID: open the channel → **View channel details** → the ID is
at the bottom. Or right-click the channel → **Copy link**; the ID is the last
path segment (`C…`).

## 8. Verify

```bash
uv run k8srca slack check
```

This checks, in the order things break:

1. the bot token is valid (`auth.test`)
2. the required scopes were granted
3. the bot can post to the channel — which also proves membership
4. Socket Mode connects (app-level token + `connections:write`)
5. inbound events actually arrive — it waits, and you post something

Step 5 is the only one that proves the **event subscriptions** are right.
Everything else can pass while no events are delivered because none were
subscribed to.

```
PASS  bot token              bot @k8srca in workspace Acme (bot_id=B0…)
PASS  scopes                 all required scopes present
PASS  channel membership     skipped (needs channels:read; the send below tests membership)
PASS  send                   posted (ts=1788892874.533499)

  Listening 45s via Socket Mode. Mention the app or post in C0123ABCD now...
PASS  receive                1 event(s):
        app_mention        user=U012ABC text='<@U0C09…> hello'
```

Useful flags: `--no-post` (skip the test message), `--listen 0` (skip the wait),
`--channel C…` (override the allowlist).

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `FAIL bot token … invalid_auth` | Token is wrong, or it's the app-level (`xapp-`) token in the bot slot. Bot tokens start `xoxb-`. |
| `FAIL scopes … missing required scopes` | Scopes added but app not reinstalled. Reinstall (step 5) and take the **new** token. |
| `FAIL send … bot is not a member` | Step 6. `/invite @k8srca` in that channel. |
| `FAIL send … channel_not_found` | Wrong ID, or a private channel the bot can't see (needs `groups:history` and an invite). |
| Socket Mode connects, **no events arrive** | Event subscriptions missing (step 4), or the bot isn't in the channel you posted in. Check `app_mention` is listed under *bot* events. |
| Nothing happens on a thread reply, but `@`-mention works | `message.channels` not subscribed, or `channels:history` not granted. |
| Event Subscriptions demands a Request URL | Socket Mode isn't actually on. Step 2. |

## What you don't need

- **Client ID / Client Secret** — for distributing the app to *other*
  workspaces via OAuth. Not needed for a single-workspace install.
- **Verification Token** — deprecated, superseded by the signing secret.
- **Signing Secret** — verifies inbound HTTP requests. Socket Mode has no
  inbound HTTP. `.env.example` carries the slot because Bolt may want it at
  init, but nothing uses it.
- **Agents / AI Apps feature** — see the top of this page.

## Security

Slack tokens are held by the **orchestrator process only**. They are never
passed into a sandbox and never reach the Anthropic control plane — see the
credential custody table in [design 001 §8](../designs/001-architecture.md).

A leaked bot token can read and post as the app in every channel it has joined.
Revoke at **OAuth & Permissions → Revoke Token**, then reinstall.
