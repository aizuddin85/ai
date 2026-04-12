"""
Base agent class — powered by Azure AI Foundry.

Each agent:
  1. Launches the official Microsoft AKS MCP server binary (github.com/Azure/aks-mcp)
     as a child process using stdio transport.
  2. Connects to it via the MCP client SDK.
  3. Discovers available tools and converts them to the Azure AI Foundry
     ChatCompletionsToolDefinition format.
  4. Runs a multi-turn conversation loop via ChatCompletionsClient,
     auto-dispatching tool calls back through the MCP client until the
     model returns finish_reason == "stop".
  5. Tears down the MCP connection and server process cleanly on exit.

Authentication for the Foundry client follows the same pattern as the
Azure credential chain:
  - If AZURE_FOUNDRY_API_KEY is set → AzureKeyCredential (key auth)
  - Otherwise → the Azure AD ChainedTokenCredential (SP or user login)

The base class is tool-agnostic; subclasses narrow the visible tool set
via tool_prefixes so each agent stays focused on its domain.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
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
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool as McpTool

from server.auth.credentials import get_azure_credential
from server.config import get_settings
from server.logging_config import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).parent.parent

# Environment variables to strip before passing the process environment to the
# aks-mcp child process.  The binary only needs Azure SDK credential vars
# (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, etc.) and standard
# system vars (PATH, HOME, KUBECONFIG …).  Foundry-specific secrets are
# application-layer secrets that the binary has no use for.
_CHILD_ENV_EXCLUDE: frozenset[str] = frozenset({
    "AZURE_FOUNDRY_API_KEY",   # Foundry auth secret – not needed by aks-mcp
    "AZURE_FOUNDRY_ENDPOINT",  # Foundry inference URL – not needed by aks-mcp
    "AZURE_FOUNDRY_MODEL",     # Foundry model name – not needed by aks-mcp
    "AZURE_ARM_TOKEN",         # Deprecated OBO token field – not used by aks-mcp
})


class BaseMcpAgent:
    """
    Base class for agents that call the official Microsoft AKS MCP server
    (github.com/Azure/aks-mcp) using Azure AI Foundry as the LLM provider.

    Subclasses should set:
      name          : human-readable agent name (used in logging).
      system_prompt : Foundry system message scoping the agent's role.
      tool_prefixes : Tuple of name prefixes; only MCP tools whose names
                      start with one of these are exposed to the model.
                      Empty tuple → all tools are exposed.
                      E.g. ("az_", "aks_") for Azure-only tools.
    """

    name: str = "base"
    system_prompt: str = "You are a helpful AKS health assistant."
    tool_prefixes: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._settings = get_settings()
        self._foundry_client = self._build_foundry_client()
        self._log = get_logger(f"agent.{self.name}")
        # Populated during run(); read by root_agent for hallucination review
        self.tool_results: list[str] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self, query: str, arm_token: str | None = None) -> str:
        """
        Process a user query end-to-end and return the final text answer.

        Opens an MCP session against the official aks-mcp binary, builds
        the tool list, runs the conversation loop, and closes the session
        before returning.

        Args:
            query:     Natural-language health question.
            arm_token: Retained for API-layer compatibility only.  The
                       official aks-mcp binary authenticates via the
                       standard Azure SDK credential chain (Service
                       Principal env vars / Workload Identity / az login)
                       and does not accept per-request token injection.
                       This parameter has no effect on the binary's auth.
        """
        self._log.info("agent.run.start", query=query[:200])
        # Reset tool results for this invocation
        self.tool_results = []

        settings = self._settings
        server_cmd = [
            settings.aks_mcp_binary,
            "--transport", "stdio",
            "--access-level", settings.aks_mcp_access_level,
        ]
        server_env = self._build_server_env()

        server_params = StdioServerParameters(
            command=server_cmd[0],
            args=server_cmd[1:],
            env=server_env,
            cwd=str(_REPO_ROOT),
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                foundry_tools = await self._discover_tools(session)
                answer = await self._conversation_loop(session, query, foundry_tools)

        self._log.info("agent.run.done", answer_length=len(answer))
        return answer

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_foundry_client(self) -> ChatCompletionsClient:
        """Build the Azure AI Foundry ChatCompletionsClient."""
        settings = self._settings
        endpoint = settings.azure_foundry_endpoint

        if settings.uses_foundry_key_auth:
            logger.info("foundry.auth.mode", mode="api_key")
            credential: Any = AzureKeyCredential(
                settings.azure_foundry_api_key.get_secret_value()  # type: ignore[union-attr]
            )
        else:
            logger.info(
                "foundry.auth.mode",
                mode="azure_ad",
                uses_sp=settings.uses_service_principal,
            )
            credential = get_azure_credential()

        return ChatCompletionsClient(endpoint=endpoint, credential=credential)

    def _build_server_env(self) -> dict[str, str]:
        """
        Build the environment for the aks-mcp child process.

        Passes through Azure credential env vars (AZURE_TENANT_ID,
        AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_FEDERATED_TOKEN_FILE,
        PATH, HOME, KUBECONFIG, etc.) so the binary can authenticate via
        the standard Azure SDK credential chain.

        Foundry-specific secrets and the deprecated AZURE_ARM_TOKEN are
        excluded — they are not needed by the binary and should not be
        accessible to a sub-process beyond this application layer.
        """
        return {k: v for k, v in os.environ.items() if k not in _CHILD_ENV_EXCLUDE}

    async def _discover_tools(
        self, session: ClientSession
    ) -> list[ChatCompletionsToolDefinition]:
        """
        Fetch tools from the MCP server and convert them to the
        Azure AI Foundry ChatCompletionsToolDefinition format.
        Applies self.tool_prefixes filter when non-empty.
        """
        mcp_response = await session.list_tools()
        mcp_tools: list[McpTool] = mcp_response.tools

        foundry_tools: list[ChatCompletionsToolDefinition] = []
        for tool in mcp_tools:
            if self.tool_prefixes and not any(
                tool.name.startswith(p) for p in self.tool_prefixes
            ):
                continue
            foundry_tools.append(
                ChatCompletionsToolDefinition(
                    function=FunctionDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        parameters=tool.inputSchema,
                    )
                )
            )

        self._log.info(
            "agent.tools.discovered",
            total_mcp=len(mcp_tools),
            filtered=len(foundry_tools),
            prefixes=self.tool_prefixes or "(all)",
        )
        return foundry_tools

    async def _conversation_loop(
        self,
        session: ClientSession,
        query: str,
        tools: list[ChatCompletionsToolDefinition],
    ) -> str:
        """
        Agentic loop using Azure AI Foundry ChatCompletionsClient:
          1. Send messages + tools to the model.
          2. If finish_reason == "tool_calls", execute each tool via MCP.
          3. Append tool results and loop.
          4. Return the final text when finish_reason == "stop".
        """
        settings = self._settings
        messages: list[Any] = [
            SystemMessage(content=self.system_prompt),
            UserMessage(content=query),
        ]
        max_iterations = 10

        for iteration in range(max_iterations):
            self._log.debug("agent.loop.iteration", i=iteration, messages=len(messages))

            response = self._foundry_client.complete(
                model=settings.azure_foundry_model,
                messages=messages,
                tools=tools if tools else None,
                max_tokens=4096,
                temperature=0.0,  # deterministic for health reporting
            )

            choice = response.choices[0]
            finish_reason = choice.finish_reason
            message = choice.message

            if (
                finish_reason == CompletionsFinishReason.STOPPED
                or not getattr(message, "tool_calls", None)
            ):
                return message.content or ""

            # Append assistant message with tool calls
            messages.append(
                AssistantMessage(
                    content=message.content,
                    tool_calls=message.tool_calls,
                )
            )

            # Execute each tool call and collect results
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                result_content = await self._execute_tool(session, tool_name, tool_args)
                self._log.info(
                    "agent.tool.executed",
                    tool=tool_name,
                    call_id=tool_call.id,
                    result_length=len(result_content),
                )
                messages.append(
                    ToolMessage(
                        tool_call_id=tool_call.id,
                        content=result_content,
                    )
                )

        self._log.warning("agent.loop.max_iterations_reached", max=max_iterations)
        return "Maximum iterations reached. Partial results may be incomplete."

    async def _execute_tool(
        self, session: ClientSession, tool_name: str, tool_input: dict[str, Any]
    ) -> str:
        """Call an MCP tool, record the result for hallucination review, and return it."""
        self._log.info("agent.tool.call", tool=tool_name)
        try:
            result = await session.call_tool(tool_name, arguments=tool_input)
            parts: list[str] = []
            for item in result.content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                else:
                    parts.append(str(item))
            content = "\n".join(parts)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("agent.tool.error", tool=tool_name, error=str(exc))
            content = json.dumps({"error": f"Tool execution failed: {exc}"})

        # Collect raw tool output for downstream hallucination review
        self.tool_results.append(f"=== Tool: {tool_name} ===\n{content}")
        return content
