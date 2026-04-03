#!/usr/bin/env python3
"""
SRE Discord Bot — Kubernetes operations powered by Claude claude-opus-4-6.

Slash commands
──────────────
/ask <question>               Free-form SRE question (multi-turn chat per channel)
/healthcheck                  Full cluster health check → Markdown report
/upgrade [node] [dry_run] [ssh_user]  Rolling OS upgrade
/nodes                        Quick node status
/pods [namespace]             Pod status in a namespace
/clear                        Reset conversation history for this channel

Environment variables
─────────────────────
DISCORD_TOKEN     Bot token from the Discord Developer Portal  (required)
ANTHROPIC_API_KEY Anthropic API key                            (required)
ALLOWED_GUILD_IDS Comma-separated guild IDs to restrict the bot (optional)
VERBOSE_TOOLS     Set to "1" to show tool call details in all commands (optional)
"""

import asyncio
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import k8s_tools
from sre_agent import SREAgent

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
MAX_MSG_LEN   = 1900          # Discord limit is 2000; leave safety margin
VERBOSE_TOOLS = os.getenv("VERBOSE_TOOLS", "0") == "1"
# Restrict to specific guilds (servers) when set — helps during development
_GUILD_IDS = [
    int(g) for g in os.getenv("ALLOWED_GUILD_IDS", "").split(",") if g.strip()
]
GUILDS = [discord.Object(id=g) for g in _GUILD_IDS] if _GUILD_IDS else []

# Per-channel conversation history for /ask (channel_id → message list)
_conversation_history: dict[int, list] = {}
# Active agent tasks per channel (channel_id → asyncio.Task)
_active_tasks: dict[int, asyncio.Task] = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("sre-bot")

# ── Discord client ─────────────────────────────────────────────────────────────

intents            = discord.Intents.default()
intents.message_content = True
bot                = commands.Bot(command_prefix="!", intents=intents)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _split(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Split text into chunks that fit within Discord's message size limit."""
    if not text.strip():
        return []
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Prefer to break at a newline
        idx = text.rfind("\n", 0, max_len)
        if idx <= 0:
            idx = max_len
        chunks.append(text[:idx])
        text = text[idx:].lstrip("\n")
    return [c for c in chunks if c.strip()]


async def _send(interaction: discord.Interaction, text: str) -> None:
    """Send text to Discord, splitting into multiple messages if needed."""
    for chunk in _split(text):
        await interaction.followup.send(chunk)


async def _run_agent(
    interaction: discord.Interaction,
    agent: SREAgent,
    task: str,
    system_prompt: str,
    history: Optional[list],
    show_tools: bool = False,
) -> list:
    """
    Run the agent and relay output to Discord.

    Returns the updated message history.
    """
    async def on_progress(text: str) -> None:
        await _send(interaction, text)

    async def on_tool_call(name: str, args: dict, result: str) -> None:
        if show_tools:
            preview = result.replace("\n", " ")[:120]
            if len(result) > 120:
                preview += "…"
            line = f"🔧 **`{name}`** — `{preview}`"
            await interaction.followup.send(line[:MAX_MSG_LEN])
        else:
            # Minimal progress indicator
            await interaction.followup.send(f"🔧 `{name}`…")

    # Re-attach callbacks to the agent
    agent.on_progress  = on_progress
    agent.on_tool_call = on_tool_call

    try:
        return await agent.run(task=task, system_prompt=system_prompt, history=history)
    except anthropic_error() as exc:
        await interaction.followup.send(f"❌ **Claude API error:** {exc}")
        return list(history or [])
    except Exception as exc:  # noqa: BLE001
        log.exception("Agent error")
        await interaction.followup.send(f"❌ **Unexpected error:** {exc}")
        return list(history or [])


def anthropic_error():
    """Return the base Anthropic exception class (avoids top-level import)."""
    import anthropic as _a
    return _a.APIError


def _guard_concurrent(channel_id: int) -> bool:
    """Return True (and cancel old task) if we should proceed."""
    old = _active_tasks.get(channel_id)
    if old and not old.done():
        old.cancel()
        log.info("Cancelled previous agent task for channel %d", channel_id)
    return True


# ── Bot lifecycle ──────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    if GUILDS:
        for guild in GUILDS:
            bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=GUILDS[0])
        log.info("Commands synced to %d guild(s)", len(GUILDS))
    else:
        await bot.tree.sync()
        log.info("Commands synced globally")
    log.info("SRE Bot ready as %s (id=%d)", bot.user, bot.user.id)


# ── Slash commands ─────────────────────────────────────────────────────────────

@bot.tree.command(
    name="ask",
    description="Ask the SRE agent anything about your Kubernetes cluster",
)
@app_commands.describe(question="Your question or task (supports multi-turn conversation)")
async def cmd_ask(interaction: discord.Interaction, question: str) -> None:
    await interaction.response.defer()
    _guard_concurrent(interaction.channel_id)

    channel_id = interaction.channel_id
    history    = _conversation_history.get(channel_id, [])
    agent, prompt = SREAgent.for_chat(
        on_progress=lambda _: None,
        on_tool_call=lambda *_: None,
    )

    await interaction.followup.send(f"💬 **{question[:200]}**")

    new_history = await _run_agent(
        interaction, agent, question, prompt,
        history=history,
        show_tools=VERBOSE_TOOLS,
    )

    # Keep the last 30 messages to avoid blowing the context window
    _conversation_history[channel_id] = new_history[-30:]


@bot.tree.command(
    name="healthcheck",
    description="Run a full Kubernetes cluster health check and produce a Markdown report",
)
async def cmd_healthcheck(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    _guard_concurrent(interaction.channel_id)

    await interaction.followup.send("🔍 Starting cluster health check…")

    agent, prompt = SREAgent.for_healthcheck(
        on_progress=lambda _: None,
        on_tool_call=lambda *_: None,
    )
    await _run_agent(
        interaction, agent,
        task=(
            "Perform a full health check on the current Kubernetes cluster. "
            "Inspect every namespace and produce a detailed Markdown report."
        ),
        system_prompt=prompt,
        history=None,
        show_tools=True,
    )


@bot.tree.command(
    name="upgrade",
    description="Rolling OS upgrade on cluster nodes (default: dry-run)",
)
@app_commands.describe(
    node="Specific node name to upgrade (omit = all nodes)",
    dry_run="Preview changes only — no actual upgrades/reboots (default True)",
    ssh_user="SSH username for cluster nodes (default: ubuntu)",
)
async def cmd_upgrade(
    interaction: discord.Interaction,
    node: Optional[str] = None,
    dry_run: bool = True,
    ssh_user: str = "ubuntu",
) -> None:
    await interaction.response.defer()
    _guard_concurrent(interaction.channel_id)

    k8s_tools.set_dry_run(dry_run)
    k8s_tools.set_ssh_user(ssh_user)

    mode  = "**DRY-RUN** (no changes)" if dry_run else "🚨 **LIVE** — changes WILL be made"
    scope = f"node `{node}`" if node else "**all nodes**"
    await interaction.followup.send(
        f"🚀 Starting rolling OS upgrade\n"
        f"Mode:  {mode}\n"
        f"Scope: {scope}\n"
        f"SSH user: `{ssh_user}`"
    )

    scope_msg  = f"Upgrade only node: {node}." if node else "Upgrade all nodes."
    dry_note   = " DRY-RUN: no changes will be made." if dry_run else ""
    task       = (
        f"Perform a rolling OS upgrade on the current Kubernetes cluster. "
        f"{scope_msg}{dry_note} "
        "Follow the drain-before-reboot procedure and produce a final Markdown report."
    )

    agent, prompt = SREAgent.for_upgrade(
        on_progress=lambda _: None,
        on_tool_call=lambda *_: None,
        ssh_user=ssh_user,
        dry_run=dry_run,
        target_node=node,
    )
    await _run_agent(
        interaction, agent,
        task=task,
        system_prompt=prompt,
        history=None,
        show_tools=True,
    )


@bot.tree.command(
    name="nodes",
    description="Quick status of all cluster nodes",
)
async def cmd_nodes(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    _guard_concurrent(interaction.channel_id)

    agent, prompt = SREAgent.for_chat(
        on_progress=lambda _: None,
        on_tool_call=lambda *_: None,
    )
    await _run_agent(
        interaction, agent,
        task=(
            "Check the status of all nodes in the cluster. "
            "List each node with its role, status, Kubernetes version, and internal IP. "
            "Flag any nodes that are not Ready."
        ),
        system_prompt=prompt,
        history=None,
        show_tools=False,
    )


@bot.tree.command(
    name="pods",
    description="Check pod status in a Kubernetes namespace",
)
@app_commands.describe(namespace="Namespace to inspect (default: default)")
async def cmd_pods(
    interaction: discord.Interaction,
    namespace: str = "default",
) -> None:
    await interaction.response.defer()
    _guard_concurrent(interaction.channel_id)

    agent, prompt = SREAgent.for_chat(
        on_progress=lambda _: None,
        on_tool_call=lambda *_: None,
    )
    await _run_agent(
        interaction, agent,
        task=(
            f"Check the status of all pods in namespace '{namespace}'. "
            "List each pod with its status, readiness, and restart count. "
            "Highlight any pods that are not Running or Completed, "
            "or that have high restart counts."
        ),
        system_prompt=prompt,
        history=None,
        show_tools=False,
    )


@bot.tree.command(
    name="clear",
    description="Reset the conversation history for this channel",
)
async def cmd_clear(interaction: discord.Interaction) -> None:
    _conversation_history.pop(interaction.channel_id, None)
    old = _active_tasks.pop(interaction.channel_id, None)
    if old and not old.done():
        old.cancel()
    await interaction.response.send_message(
        "🗑️ Conversation history cleared.", ephemeral=True
    )


# ── Prefix command: !kubectl ───────────────────────────────────────────────────
# Useful for quick read-only kubectl queries without using the full agent loop.

@bot.command(name="kubectl")
async def prefix_kubectl(ctx: commands.Context, *args: str) -> None:
    """Run a read-only kubectl command directly.  Usage: !kubectl get pods -n kube-system"""
    if not args:
        await ctx.reply("Usage: `!kubectl <args…>`")
        return

    # Block write verbs
    dangerous = {"delete", "patch", "edit", "apply", "replace", "create", "exec", "run"}
    if args[0].lower() in dangerous:
        await ctx.reply(
            f"⛔ `{args[0]}` is a write operation and is not allowed via `!kubectl`. "
            "Use `/ask` to request changes through the SRE agent."
        )
        return

    async with ctx.typing():
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, k8s_tools._kubectl, list(args), 60
        )

    for chunk in _split(f"```\n{result}\n```"):
        await ctx.reply(chunk)


# ── Error handler ──────────────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    msg = f"❌ Command error: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
    log.error("App command error: %s", error, exc_info=error)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN environment variable is not set.")
    bot.run(DISCORD_TOKEN, log_handler=None)
