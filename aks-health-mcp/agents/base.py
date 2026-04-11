"""
Base agent class.

Each agent:
  1. Launches the MCP server as a child process (stdio transport).
  2. Connects to it via the MCP client SDK.
  3. Discovers available tools and converts them to Anthropic tool format.
  4. Runs a multi-turn Claude conversation, auto-dispatching tool calls
     back through the MCP client until the model returns a final answer.
  5. Tears down the MCP connection and server process cleanly on exit.

The base class is intentionally tool-agnostic; subclasses limit the
tool set they expose to Claude so each agent stays focused.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

import anthropic
import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool as McpTool

from server.config import get_settings
from server.logging_config import get_logger

logger = get_logger(__name__)

# Server launch command (resolve to absolute path for safety)
_SERVER_CMD = [
    sys.executable,
    "-m",
    "server.main",
]
_REPO_ROOT = Path(__file__).parent.parent


class BaseMcpAgent:
    """
    Base class for agents that call the AKS Health MCP server.

    Subclasses should set:
      - name        : human-readable agent name (for logging)
      - system_prompt: Claude system prompt scoping the agent's role
      - tool_prefix  : if non-empty, only MCP tools whose names start
                       with this prefix are made available to Claude.
                       E.g. "aks_" for Azure-only, "k8s_" for cluster-only.
                       Leave empty ("") to expose all tools.
    """

    name: str = "base"
    system_prompt: str = "You are a helpful AKS health assistant."
    tool_prefix: str = ""  # empty → all tools

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = anthropic.Anthropic(
            api_key=self._settings.anthropic_api_key.get_secret_value()
        )
        self._log = get_logger(f"agent.{self.name}")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self, query: str) -> str:
        """
        Process a user query end-to-end and return the final text answer.

        Opens an MCP session, builds the tool list, runs the conversation
        loop, and closes the session before returning.
        """
        self._log.info("agent.run.start", query=query[:200])
        server_env = self._build_server_env()

        server_params = StdioServerParameters(
            command=_SERVER_CMD[0],
            args=_SERVER_CMD[1:],
            env=server_env,
            cwd=str(_REPO_ROOT),
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                claude_tools = await self._discover_tools(session)
                answer = await self._conversation_loop(session, query, claude_tools)

        self._log.info("agent.run.done", answer_length=len(answer))
        return answer

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_server_env(self) -> dict[str, str]:
        """
        Build the environment for the MCP server child process.
        Passes all required env vars; never logs secrets.
        """
        env = dict(os.environ)
        # Always use stdio when launched as a subprocess by an agent
        env["MCP_TRANSPORT"] = "stdio"
        # Ensure pythonpath includes the project root
        env["PYTHONPATH"] = str(_REPO_ROOT)
        return env

    async def _discover_tools(self, session: ClientSession) -> list[dict[str, Any]]:
        """
        Fetch tools from the MCP server and convert to Anthropic format.
        Applies self.tool_prefix filter.
        """
        mcp_tools_response = await session.list_tools()
        mcp_tools: list[McpTool] = mcp_tools_response.tools

        claude_tools: list[dict[str, Any]] = []
        for tool in mcp_tools:
            if self.tool_prefix and not tool.name.startswith(self.tool_prefix):
                continue
            claude_tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
            )

        self._log.info(
            "agent.tools.discovered",
            total_mcp=len(mcp_tools),
            filtered=len(claude_tools),
            prefix=self.tool_prefix or "(all)",
        )
        return claude_tools

    async def _conversation_loop(
        self,
        session: ClientSession,
        query: str,
        tools: list[dict[str, Any]],
    ) -> str:
        """
        Run the agentic loop:
          1. Send messages to Claude with available tools.
          2. Execute any tool_use blocks via the MCP client.
          3. Feed results back to Claude.
          4. Repeat until Claude returns stop_reason == "end_turn".
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
        max_iterations = 10  # guard against infinite loops

        for iteration in range(max_iterations):
            self._log.debug("agent.loop.iteration", i=iteration, messages=len(messages))

            response = self._client.messages.create(
                model=self._settings.claude_model,
                max_tokens=4096,
                system=self.system_prompt,
                tools=tools,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )

            # Collect all content blocks from the response
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if response.stop_reason == "end_turn" or not tool_uses:
                # Return the final text answer
                return " ".join(b.text for b in text_blocks).strip()

            # Append assistant turn (with all content blocks)
            messages.append(
                {
                    "role": "assistant",
                    "content": [b.model_dump() for b in response.content],
                }
            )

            # Execute tool calls and collect results
            tool_results: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                result_content = await self._execute_tool(session, tool_use.name, tool_use.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result_content,
                    }
                )
                self._log.info(
                    "agent.tool.executed",
                    tool=tool_use.name,
                    result_length=len(result_content),
                )

            # Append user turn with all tool results
            messages.append({"role": "user", "content": tool_results})

        self._log.warning("agent.loop.max_iterations_reached", max=max_iterations)
        return "Maximum iterations reached. Partial results may be incomplete."

    async def _execute_tool(
        self, session: ClientSession, tool_name: str, tool_input: dict[str, Any]
    ) -> str:
        """Call an MCP tool and return the result as a string."""
        self._log.info("agent.tool.call", tool=tool_name)
        try:
            result = await session.call_tool(tool_name, arguments=tool_input)
            # MCP result content is a list of content objects
            parts: list[str] = []
            for item in result.content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("agent.tool.error", tool=tool_name, error=str(exc))
            return json.dumps({"error": f"Tool execution failed: {exc}"})
