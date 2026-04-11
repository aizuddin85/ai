"""
Root Orchestrator Agent
=======================
The root agent receives high-level health queries and decides how to
delegate them to specialist task agents:

  - AzureHealthAgent  – Azure Resource Manager perspective
  - ClusterHealthAgent – Live Kubernetes API perspective

Architecture:
  root agent (Claude)
    ├── tool: query_azure_health   → AzureHealthAgent → MCP server (aks_* tools)
    └── tool: query_cluster_health → ClusterHealthAgent → MCP server (k8s_* tools)

The root agent synthesises the responses from both sub-agents into a
coherent overall health summary with actionable findings and remediation
suggestions.

Usage:
    import asyncio
    from agents.root_agent import RootAgent

    async def main():
        agent = RootAgent()
        result = await agent.run(
            "What is the overall health of my AKS clusters? "
            "Highlight any issues I should act on immediately."
        )
        print(result)

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic
import structlog

from agents.task_agents.azure_health_agent import AzureHealthAgent
from agents.task_agents.cluster_health_agent import ClusterHealthAgent
from server.config import get_settings
from server.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are an AKS Operations Commander – a senior SRE who provides a
comprehensive, actionable assessment of AKS cluster health by
coordinating two specialist sub-agents:

  1. azure_health_agent – Queries the Azure control plane for cluster
     provisioning state, node pool health, resource health events, and
     Azure Monitor metrics.

  2. cluster_health_agent – Queries the live Kubernetes API for node
     readiness, workload health (pods, deployments, DaemonSets,
     StatefulSets), cluster events, and PVC status.

Your workflow:
  1. Analyse the user's question.
  2. Decide which sub-agents to invoke (one or both).
  3. Invoke them with focused, specific sub-queries.
  4. Synthesise their findings into a single coherent report.
  5. Prioritise issues by business impact (service disruption > degraded
     performance > informational).
  6. Provide clear, actionable remediation steps for each finding.

Guidelines:
  - Always invoke BOTH agents when the user asks for overall health.
  - Invoke only the relevant agent for narrow questions
    (e.g. "are my pods healthy?" → cluster_health_agent only).
  - Never fabricate data; base your report entirely on the sub-agents'
    responses.
  - When sub-agents return errors, include them in the report with
    suggested investigation steps.
  - Format the final report as structured Markdown:
      ## Executive Summary
      ## Critical Issues  (if any)
      ## Warnings         (if any)
      ## Informational
      ## Recommended Actions

Output: a single, comprehensive Markdown health report.
"""

# Tool definitions passed to the root Claude agent
_ROOT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "query_azure_health",
        "description": (
            "Delegate a query to the Azure AKS Health Agent, which uses the "
            "Azure Resource Manager API to check cluster provisioning state, "
            "node pool status, resource health events, upgrade profiles, and "
            "Azure Monitor metrics. Use this for Azure control-plane questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The specific health question to ask the Azure agent, e.g.: "
                        "'List all clusters and their provisioning states' or "
                        "'Are there any active resource health events?'"
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_cluster_health",
        "description": (
            "Delegate a query to the Cluster Health Agent, which uses the "
            "live Kubernetes API to check node readiness, pod health, "
            "deployment/DaemonSet/StatefulSet status, events, PVCs, and "
            "control-plane component health. Use this for workload-level questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The specific health question to ask the Kubernetes agent, e.g.: "
                        "'List all pods not in Running state' or "
                        "'Are there any Warning events in the last hour?'"
                    ),
                }
            },
            "required": ["query"],
        },
    },
]


class RootAgent:
    """
    Root orchestrator agent.

    Coordinates AzureHealthAgent and ClusterHealthAgent to answer
    comprehensive AKS health questions.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        self._model = settings.claude_model
        self._azure_agent = AzureHealthAgent()
        self._cluster_agent = ClusterHealthAgent()
        self._log = get_logger("agent.root")

    async def run(self, query: str) -> str:
        """
        Process a high-level AKS health query and return a synthesised report.

        Args:
            query: Natural-language health question, e.g.
                   "What is the overall health of my AKS environment?"

        Returns:
            Formatted Markdown health report.
        """
        self._log.info("root_agent.run.start", query=query[:200])
        messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
        max_iterations = 6

        for iteration in range(max_iterations):
            self._log.debug("root_agent.loop.iteration", i=iteration)

            response = self._client.messages.create(
                model=self._model,
                max_tokens=8192,
                system=_SYSTEM_PROMPT,
                tools=_ROOT_TOOLS,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if response.stop_reason == "end_turn" or not tool_uses:
                final_answer = " ".join(b.text for b in text_blocks).strip()
                self._log.info("root_agent.run.done", answer_length=len(final_answer))
                return final_answer

            # Append assistant turn
            messages.append(
                {
                    "role": "assistant",
                    "content": [b.model_dump() for b in response.content],
                }
            )

            # Dispatch sub-agent calls concurrently for efficiency
            tool_results = await self._dispatch_tools(tool_uses)

            messages.append({"role": "user", "content": tool_results})

        self._log.warning("root_agent.max_iterations_reached", max=max_iterations)
        return "Maximum orchestration iterations reached. Please narrow your query."

    async def _dispatch_tools(
        self, tool_uses: list[Any]
    ) -> list[dict[str, Any]]:
        """
        Run all tool calls concurrently and collect results.
        Parallel execution reduces overall latency when both agents are needed.
        """
        tasks = [self._call_sub_agent(tu.id, tu.name, tu.input) for tu in tool_uses]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_results: list[dict[str, Any]] = []
        for i, result in enumerate(results):
            tool_use_id = tool_uses[i].id
            if isinstance(result, Exception):
                content = json.dumps({"error": str(result)})
            else:
                content = str(result)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                }
            )

        return tool_results

    async def _call_sub_agent(
        self, tool_use_id: str, tool_name: str, tool_input: dict[str, Any]
    ) -> str:
        """Route a tool call to the appropriate sub-agent."""
        sub_query: str = tool_input.get("query", "")
        self._log.info("root_agent.sub_agent.call", tool=tool_name, query=sub_query[:100])

        if tool_name == "query_azure_health":
            return await self._azure_agent.run(sub_query)
        elif tool_name == "query_cluster_health":
            return await self._cluster_agent.run(sub_query)
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _cli_main() -> None:
    import sys

    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_format="console")

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "What is the overall health of my AKS environment? "
        "List any critical issues and recommended actions."
    )

    print(f"\nQuery: {query}\n{'=' * 60}\n")
    agent = RootAgent()
    result = await agent.run(query)
    print(result)


if __name__ == "__main__":
    asyncio.run(_cli_main())
