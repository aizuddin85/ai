"""
Root Orchestrator Agent — powered by Azure AI Foundry.
=====================================================
The root agent receives high-level health queries and coordinates two
specialist task agents through a Foundry ChatCompletionsClient tool-calling
loop:

  - AzureHealthAgent  – Azure Resource Manager perspective (aks_* tools)
  - ClusterHealthAgent – Live Kubernetes API perspective (k8s_* tools)

Architecture:
  root agent (Azure AI Foundry model)
    ├── tool: query_azure_health   → AzureHealthAgent → MCP server (aks_*)
    └── tool: query_cluster_health → ClusterHealthAgent → MCP server (k8s_*)

Sub-agent calls run concurrently (asyncio.gather) to minimise latency.

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

import structlog
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import (
    AssistantMessage,
    ChatCompletionsToolDefinition,
    CompletionsFinishReason,
    FunctionDefinition,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from azure.core.credentials import AzureKeyCredential

from agents.reviewer import review_response
from agents.task_agents.azure_health_agent import AzureHealthAgent
from agents.task_agents.cluster_health_agent import ClusterHealthAgent
from server.auth.credentials import get_azure_credential
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
  - Invoke only the relevant agent for narrow questions.
  - Never fabricate data; base your report entirely on sub-agent responses.
  - When sub-agents return errors, include them with suggested next steps.
  - Format the final report as structured Markdown:
      ## Executive Summary
      ## Critical Issues  (if any)
      ## Warnings         (if any)
      ## Informational
      ## Recommended Actions
"""

# Tool definitions given to the root Foundry model
_ROOT_TOOLS: list[ChatCompletionsToolDefinition] = [
    ChatCompletionsToolDefinition(
        function=FunctionDefinition(
            name="query_azure_health",
            description=(
                "Delegate a query to the Azure AKS Health Agent, which uses the "
                "Azure Resource Manager API to check cluster provisioning state, "
                "node pool status, resource health events, upgrade profiles, and "
                "Azure Monitor metrics. Use this for Azure control-plane questions."
            ),
            parameters={
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
        )
    ),
    ChatCompletionsToolDefinition(
        function=FunctionDefinition(
            name="query_cluster_health",
            description=(
                "Delegate a query to the Cluster Health Agent, which uses the "
                "live Kubernetes API to check node readiness, pod health, "
                "deployment/DaemonSet/StatefulSet status, events, PVCs, and "
                "control-plane component health. Use this for workload-level questions."
            ),
            parameters={
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
        )
    ),
]


class RootAgent:
    """
    Root orchestrator agent powered by Azure AI Foundry.

    Coordinates AzureHealthAgent and ClusterHealthAgent to answer
    comprehensive AKS health questions using the Foundry ChatCompletionsClient.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._model = settings.azure_foundry_model
        self._foundry_client = self._build_client(settings)
        self._azure_agent = AzureHealthAgent()
        self._cluster_agent = ClusterHealthAgent()
        self._log = get_logger("agent.root")
        # Accumulated raw tool results from all sub-agents (for hallucination review)
        self._all_tool_results: list[str] = []

    @staticmethod
    def _build_client(settings: Any) -> ChatCompletionsClient:
        endpoint = settings.azure_foundry_endpoint
        if settings.uses_foundry_key_auth:
            logger.info("root_agent.foundry.auth", mode="api_key")
            credential: Any = AzureKeyCredential(
                settings.azure_foundry_api_key.get_secret_value()
            )
        else:
            logger.info(
                "root_agent.foundry.auth",
                mode="azure_ad",
                uses_sp=settings.uses_service_principal,
            )
            credential = get_azure_credential()
        return ChatCompletionsClient(endpoint=endpoint, credential=credential)

    async def run(
        self,
        query: str,
        arm_token: str | None = None,
        k8s_token: str | None = None,
    ) -> str:
        """
        Process a high-level AKS health query and return a synthesised
        Markdown report.

        Args:
            query:     Natural-language health question.
            arm_token: OBO ARM token propagated to sub-agents so Azure SDK
                       calls execute as the signed-in user.
            k8s_token: OBO AKS Kubernetes API token propagated to sub-agents
                       so Kubernetes API calls use Azure RBAC (no SA needed).

        Returns:
            Formatted Markdown health report.
        """
        self._log.info("root_agent.run.start", query=query[:200])
        self._arm_token = arm_token
        self._k8s_token = k8s_token
        self._all_tool_results = []  # reset for this invocation
        messages: list[Any] = [
            SystemMessage(content=_SYSTEM_PROMPT),
            UserMessage(content=query),
        ]
        max_iterations = 6

        for iteration in range(max_iterations):
            self._log.debug("root_agent.loop.iteration", i=iteration)

            response = self._foundry_client.complete(
                model=self._model,
                messages=messages,
                tools=_ROOT_TOOLS,
                max_tokens=8192,
                temperature=0.0,
            )

            choice = response.choices[0]
            finish_reason = choice.finish_reason
            message = choice.message

            if (
                finish_reason == CompletionsFinishReason.STOPPED
                or not getattr(message, "tool_calls", None)
            ):
                draft = message.content or ""
                self._log.info("root_agent.draft_ready", draft_length=len(draft))

                # Run hallucination review before returning to the user
                final_answer = await review_response(
                    query=query,
                    tool_results="\n\n".join(self._all_tool_results),
                    draft_answer=draft,
                    settings=self._settings,
                )
                self._log.info("root_agent.run.done", answer_length=len(final_answer))
                return final_answer

            # Append assistant turn with tool call info
            messages.append(
                AssistantMessage(
                    content=message.content,
                    tool_calls=message.tool_calls,
                )
            )

            # Run all sub-agent calls concurrently
            tool_results = await self._dispatch_tools(message.tool_calls)
            messages.extend(tool_results)

        self._log.warning("root_agent.max_iterations_reached", max=max_iterations)
        return "Maximum orchestration iterations reached. Please narrow your query or try again."

    async def _dispatch_tools(
        self, tool_calls: list[Any]
    ) -> list[ToolMessage]:
        """
        Execute all tool calls concurrently and return ToolMessage objects.
        Parallel execution minimises wall-clock time when both agents are needed.
        """
        tasks = [
            self._call_sub_agent(tc.id, tc.function.name, tc.function.arguments)
            for tc in tool_calls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tool_messages: list[ToolMessage] = []
        for tc, result in zip(tool_calls, results):
            if isinstance(result, Exception):
                content = json.dumps({"error": str(result)})
            else:
                content = str(result)
            tool_messages.append(
                ToolMessage(tool_call_id=tc.id, content=content)
            )
        return tool_messages

    async def _call_sub_agent(
        self, tool_use_id: str, tool_name: str, arguments_str: str
    ) -> str:
        """Route a tool call to the appropriate sub-agent."""
        try:
            args = json.loads(arguments_str) if arguments_str else {}
        except (json.JSONDecodeError, TypeError):
            args = {}

        sub_query: str = args.get("query", "")
        self._log.info("root_agent.sub_agent.call", tool=tool_name, query=sub_query[:100])

        arm_token = getattr(self, "_arm_token", None)
        k8s_token = getattr(self, "_k8s_token", None)

        if tool_name == "query_azure_health":
            result = await self._azure_agent.run(sub_query, arm_token=arm_token)
            # Accumulate raw MCP tool results for hallucination review
            self._all_tool_results.extend(self._azure_agent.tool_results)
            return result
        elif tool_name == "query_cluster_health":
            result = await self._cluster_agent.run(
                sub_query, arm_token=arm_token, k8s_token=k8s_token
            )
            self._all_tool_results.extend(self._cluster_agent.tool_results)
            return result
        else:
            return json.dumps({"error": f"Unknown sub-agent tool: {tool_name}"})


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
