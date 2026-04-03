# SRE Discord Bot

An agentic AI assistant that acts as a Kubernetes SRE (Site Reliability Engineer)
in your Discord server.  It is powered by **Claude claude-opus-4-6** with adaptive thinking
and uses a full suite of `kubectl` and SSH tools to inspect and manage your cluster.

```
User in Discord          Claude claude-opus-4-6 (Anthropic)
     │                           │
     │  /healthcheck             │
     │──────────────────────────►│
     │                           │  list_namespaces()
     │                           │  check_pods(namespace="kube-system")
     │                           │  check_events(namespace="default")
     │                           │  describe_resource(pod, "crash-pod")
     │                           │  …
     │  ◄─── Markdown report ────│
```

---

## Architecture

| File | Purpose |
|---|---|
| `bot.py` | Discord client, slash-command handlers, message routing |
| `sre_agent.py` | Async Claude agent loop, system prompts |
| `k8s_tools.py` | All `kubectl` + SSH tool definitions and implementations |

The agent runs an **agentic tool-use loop**: Claude decides which tools to call,
the bot executes them (in a thread pool so blocking subprocess calls do not freeze
the async event loop), feeds results back, and repeats until Claude produces a
final answer.

Per-channel **conversation history** is maintained for `/ask` so engineers can
have multi-turn sessions ("what about the default namespace?" after a health check).

---

## Prerequisites

- Python 3.11+
- `kubectl` on `PATH`, configured with a valid `kubeconfig`
- SSH key access to cluster nodes (for upgrade commands)
- An [Anthropic API key](https://console.anthropic.com)
- A [Discord bot token](https://discord.com/developers/applications)

---

## Quick Start

```bash
# 1 — Clone and enter the directory
cd claude-agentic/discord-sre-bot

# 2 — Install dependencies
pip install -r requirements.txt

# 3 — Configure environment variables
cp .env.example .env
$EDITOR .env          # fill in DISCORD_TOKEN and ANTHROPIC_API_KEY

# 4 — Run the bot
python bot.py
```

The bot syncs slash commands on startup.  If `ALLOWED_GUILD_IDS` is set, commands
appear instantly in those servers.  Without it, global sync can take up to one hour.

---

## Discord Bot Setup

1. Go to <https://discord.com/developers/applications> and create a new application.
2. Under **Bot**, enable:
   - **Message Content Intent**
3. Under **OAuth2 → URL Generator**, select scopes:
   - `bot`
   - `applications.commands`
4. Bot permissions required:
   - Send Messages
   - Read Message History
   - Use Slash Commands
5. Copy the generated URL and invite the bot to your server.

---

## Commands

### `/ask <question>`

Free-form SRE Q&A with **multi-turn conversation** per channel.

The agent has access to all read and write tools but asks for confirmation before
destructive operations unless you explicitly authorise them in your message.

```
/ask what pods are failing in the production namespace?
/ask why is my deployment stuck at 0/3 replicas?
/ask drain node worker-2 and check what happens
```

### `/healthcheck`

Runs a full, autonomous cluster health check:

1. Detects cluster context
2. Checks all nodes
3. Inspects every namespace (pods, deployments, services, events)
4. Deep-dives into unhealthy resources
5. Produces a structured Markdown report (Executive Summary → Issues → Recommendations)

### `/upgrade [node] [dry_run] [ssh_user]`

Rolling OS upgrade across all nodes (or a single node).

| Parameter | Default | Description |
|---|---|---|
| `node` | *(all nodes)* | Upgrade only this node |
| `dry_run` | `True` | Preview only — no actual changes |
| `ssh_user` | `ubuntu` | SSH username for cluster nodes |

The upgrade procedure for each node:
`check updates → cordon → drain → apt/yum upgrade → reboot → wait ready → uncordon → verify`

Set `dry_run: False` only when you are ready to apply changes.

### `/nodes`

Quick node status table — roles, Ready/NotReady, Kubernetes version, IP.

### `/pods [namespace]`

Pod status for a namespace, highlighting non-Running pods and high restart counts.

### `/clear`

Reset the conversation history for the current channel.

### `!kubectl <args>`

Run a **read-only** kubectl command directly.

```
!kubectl get pods -n kube-system
!kubectl describe node worker-1
!kubectl top nodes
```

Write verbs (`delete`, `apply`, `patch`, `exec`, …) are blocked — use `/ask`
instead so the agent can apply appropriate safety checks.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Discord bot token |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key |
| `ALLOWED_GUILD_IDS` | — | Comma-separated server IDs for instant command sync |
| `VERBOSE_TOOLS` | — | Set `1` to show tool output for all commands |

---

## Safety Notes

- **`/upgrade` defaults to dry-run.**  You must explicitly pass `dry_run: False`
  to make real changes.
- Destructive operations via `/ask` (drain, delete, reboot) prompt for
  confirmation unless you include phrases like "go ahead", "yes", or "do it".
- The `!kubectl` prefix command blocks all write verbs.
- SSH operations use key-based auth only (`BatchMode=yes`); the bot never prompts
  for passwords.
