#!/usr/bin/env python3
"""
Async SRE agent powered by Claude claude-opus-4-6.

The agent drives an agentic tool-use loop:
  1. Sends user task + conversation history to Claude.
  2. Streams any text blocks to `on_progress`.
  3. Executes each tool call (in a thread-pool so blocking subprocess calls
     don't freeze the Discord event loop) and reports via `on_tool_call`.
  4. Loops until stop_reason == "end_turn".

Returns the full updated message history so callers can maintain
multi-turn conversations across Discord interactions.
"""

import asyncio
import json
from typing import Awaitable, Callable, Optional

import anthropic

import k8s_tools

# ── System prompts ─────────────────────────────────────────────────────────────

SRE_SYSTEM_PROMPT = """\
You are an expert Kubernetes SRE (Site Reliability Engineer) assistant integrated
into a Discord server.  You help engineers understand, monitor, debug, and manage
their Kubernetes cluster.

You have access to a full set of kubectl and SSH tools to inspect the cluster and
take actions when asked.

GUIDELINES
- Be concise but thorough.  Prefer bullet points and Markdown tables where they
  help readability.
- For read-only queries (health checks, status) act immediately using the tools.
- For WRITE or DESTRUCTIVE operations (drain, delete, upgrade, reboot) ask for
  explicit confirmation UNLESS the user's message already includes clear intent
  (e.g. "go ahead", "yes", "do it", "--live").
- When you produce a report, use Markdown with clear headings.  Sections:
  Executive Summary → Nodes → Namespaces → Issues → Recommendations.
- Always state the current cluster context at the start of any action.
- If a tool returns an error, diagnose it and try an alternative approach before
  giving up.
"""

HEALTHCHECK_SYSTEM_PROMPT = """\
You are an expert Kubernetes SRE performing a comprehensive cluster health check.

STRATEGY
1. Identify the active kubectl context (cluster, server, user).
2. Check all cluster nodes for readiness.
3. List all namespaces.
4. For each namespace:
   a. Check pod status — flag any not Running/Completed or with high restart counts.
   b. Check deployment health — flag any with unavailable replicas.
   c. Check services.
   d. Fetch Warning events.
   e. If any resource looks unhealthy, use describe_resource for details.
5. Write a structured Markdown health report:
   - Executive summary (Healthy / Degraded / Critical)
   - Nodes section
   - Per-namespace section (pods, deployments, notable events)
   - Issues found (Critical / Warning / Info)
   - Recommendations

Be thorough.  Do not skip namespaces.  Use tools as many times as needed.
"""


def _build_upgrade_system_prompt(
    ssh_user: str,
    dry_run: bool,
    target_node: Optional[str],
) -> str:
    scope = (
        f"Only upgrade the node named '{target_node}'."
        if target_node
        else "Upgrade ALL nodes in rolling order: control-plane nodes first, then worker nodes."
    )
    dry_note = (
        "\n⚠️  DRY-RUN MODE ACTIVE — no upgrades, reboots, or drain operations will execute.\n"
        if dry_run
        else ""
    )
    return f"""\
You are an expert Kubernetes SRE performing a rolling OS upgrade.
{dry_note}
SSH username: '{ssh_user}'.  SSH keys are pre-loaded; no password prompts.

SCOPE: {scope}

ROLLING UPGRADE PROCEDURE (one node at a time):
1. get_current_context      — confirm the cluster.
2. list_nodes               — get all nodes, their IPs, roles, and status.
3. For each node (control-plane first, then workers):
   a. get_node_details      — verify node is Ready before touching it.
      Skip to next node if NotReady.
   b. ssh_detect_os         — detect Debian/Ubuntu vs RHEL/CentOS/Amazon Linux.
   c. ssh_check_updates     — check for pending packages and reboot requirement.
      ► updates_available=false AND reboot_required=false → skip this node entirely.
      ► Otherwise continue d–j.
   d. cordon_node
   e. drain_node            — evict pods (ignore DaemonSets).
      → On failure: follow DRAIN RECOVERY PROCEDURE.
   f. ssh_run_upgrade       — run apt-get/yum/dnf upgrade.
   g. ssh_reboot_node       — reboot (skip if no upgrade was run AND reboot_required=false).
   h. wait_for_node_ready
   i. uncordon_node
   j. check_node_pods       — verify workloads rescheduled.
   k. Log ✅ summary for this node.
4. Final Markdown upgrade report:
   - Cluster context
   - Table: node | IP | OS | result | notes
   - Errors encountered and resolutions
   - Recommendations

DRAIN RECOVERY (attempt in order):
1. get_drain_blockers  — understand the blocker.
2. delete_stuck_pods + retry drain_node.
3. force_drain_node.
4. If force_drain also fails: uncordon node, report failure with details, STOP.

SAFETY RULES
- Never skip drain.  Never uncordon before node is Ready.
- Process exactly ONE node at a time.
- If a node starts NotReady, skip it and flag in the report.
- If all drain recovery fails, restore (uncordon) and stop.
"""


# ── Agent class ────────────────────────────────────────────────────────────────

ProgressCallback  = Callable[[str], Awaitable[None]]
ToolCallCallback  = Callable[[str, dict, str], Awaitable[None]]


class SREAgent:
    """
    Async Claude agent that drives a tool-use loop for Kubernetes SRE tasks.

    Parameters
    ----------
    on_progress:
        Async callback called with each text block Claude produces.
    on_tool_call:
        Async callback called after each tool execution with
        (tool_name, tool_input_dict, result_text).
    max_tool_output:
        Truncate tool output to this many characters before feeding back to
        Claude (keeps context manageable for long kubectl output).
    """

    def __init__(
        self,
        on_progress: ProgressCallback,
        on_tool_call: ToolCallCallback,
        max_tool_output: int = 6000,
    ) -> None:
        self.client          = anthropic.AsyncAnthropic()
        self.on_progress     = on_progress
        self.on_tool_call    = on_tool_call
        self.max_tool_output = max_tool_output

    async def run(
        self,
        task: str,
        system_prompt: str,
        history: Optional[list] = None,
    ) -> list:
        """
        Run the agent for a single user task.

        Parameters
        ----------
        task:
            The user's request (plain text).
        system_prompt:
            The system-level instructions for this run.
        history:
            Prior conversation messages to prepend (for multi-turn sessions).
            Each element must be a valid Anthropic MessageParam dict.

        Returns
        -------
        The full updated message list (history + this turn's exchange).
        This should be stored and passed back as `history` on the next call.
        """
        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": task})

        loop = asyncio.get_event_loop()

        while True:
            response = await self.client.messages.create(
                model="claude-opus-4-6",
                max_tokens=8096,
                thinking={"type": "adaptive"},
                system=system_prompt,
                tools=k8s_tools.TOOL_DEFINITIONS,
                messages=messages,
            )

            # Append full assistant content (preserves thinking blocks for
            # subsequent turns as required by the API).
            messages.append({"role": "assistant", "content": response.content})

            # Forward visible text to the caller
            for block in response.content:
                if block.type == "text" and block.text:
                    await self.on_progress(block.text)

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason != "tool_use":
                await self.on_progress(
                    f"⚠️ Unexpected stop_reason: `{response.stop_reason}`"
                )
                break

            # Execute all tool calls, collect results
            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name  = block.name
                tool_input = block.input  # already a dict

                fn = k8s_tools.TOOL_MAP.get(tool_name)
                if fn is None:
                    result_text = f"[ERROR] Unknown tool: {tool_name}"
                else:
                    try:
                        # Run the blocking subprocess in a thread pool
                        result_text = await loop.run_in_executor(
                            None, fn, tool_input
                        )
                    except Exception as exc:  # noqa: BLE001
                        result_text = f"[ERROR] Tool raised exception: {exc}"

                # Truncate to stay within context limits
                if len(result_text) > self.max_tool_output:
                    result_text = (
                        result_text[: self.max_tool_output] + "\n… (truncated)"
                    )

                # Notify the bot so it can post a progress line
                await self.on_tool_call(tool_name, tool_input, result_text)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            messages.append({"role": "user", "content": tool_results})

        return messages

    # ── Convenience factory methods ────────────────────────────────────────────

    @classmethod
    def for_healthcheck(
        cls,
        on_progress: ProgressCallback,
        on_tool_call: ToolCallCallback,
    ) -> tuple["SREAgent", str]:
        """Return (agent, system_prompt) for a cluster health check."""
        return cls(on_progress, on_tool_call), HEALTHCHECK_SYSTEM_PROMPT

    @classmethod
    def for_upgrade(
        cls,
        on_progress: ProgressCallback,
        on_tool_call: ToolCallCallback,
        ssh_user: str = "ubuntu",
        dry_run: bool = True,
        target_node: Optional[str] = None,
    ) -> tuple["SREAgent", str]:
        """Return (agent, system_prompt) for a rolling OS upgrade."""
        prompt = _build_upgrade_system_prompt(ssh_user, dry_run, target_node)
        return cls(on_progress, on_tool_call), prompt

    @classmethod
    def for_chat(
        cls,
        on_progress: ProgressCallback,
        on_tool_call: ToolCallCallback,
    ) -> tuple["SREAgent", str]:
        """Return (agent, system_prompt) for general SRE Q&A."""
        return cls(on_progress, on_tool_call), SRE_SYSTEM_PROMPT
