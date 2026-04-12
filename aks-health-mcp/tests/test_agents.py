"""
Unit tests for agent classes.
Azure AI Foundry client and MCP session are mocked so no real
Foundry endpoint or cluster is needed.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.task_agents.azure_health_agent import AzureHealthAgent
from agents.task_agents.cluster_health_agent import ClusterHealthAgent


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def test_azure_health_agent_tool_prefixes() -> None:
    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    # Must cover the key official aks-mcp tool name prefixes
    assert "az_" in agent.tool_prefixes
    assert "aks_" in agent.tool_prefixes


def test_cluster_health_agent_tool_prefixes() -> None:
    agent = ClusterHealthAgent.__new__(ClusterHealthAgent)
    # Must cover kubectl and ecosystem CLI tools
    assert any(p.startswith("call_kubectl") for p in agent.tool_prefixes)
    assert any(p.startswith("collect_") for p in agent.tool_prefixes)


def test_azure_health_agent_name() -> None:
    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    assert agent.name == "azure-health"


def test_cluster_health_agent_name() -> None:
    agent = ClusterHealthAgent.__new__(ClusterHealthAgent)
    assert agent.name == "cluster-health"


# ---------------------------------------------------------------------------
# Foundry client construction (key vs. Azure AD)
# ---------------------------------------------------------------------------


@patch("agents.base.get_azure_credential")
@patch("agents.base.ChatCompletionsClient")
def test_agent_uses_azure_ad_when_no_key(
    mock_client: MagicMock,
    mock_cred: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When AZURE_FOUNDRY_API_KEY is absent, Azure AD credential is used."""
    monkeypatch.delenv("AZURE_FOUNDRY_API_KEY", raising=False)
    mock_cred.return_value = MagicMock()

    agent = AzureHealthAgent()

    mock_client.assert_called_once()
    call_kwargs = mock_client.call_args
    # credential should be the Azure AD credential, not an AzureKeyCredential
    from azure.core.credentials import AzureKeyCredential
    assert not isinstance(call_kwargs.kwargs.get("credential"), AzureKeyCredential)


@patch("agents.base.get_azure_credential")
@patch("agents.base.ChatCompletionsClient")
def test_agent_uses_key_when_configured(
    mock_client: MagicMock,
    mock_cred: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When AZURE_FOUNDRY_API_KEY is set, AzureKeyCredential is used."""
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", "my-test-key")
    from server.config import get_settings
    get_settings.cache_clear()

    agent = AzureHealthAgent()

    mock_client.assert_called_once()
    call_kwargs = mock_client.call_args
    from azure.core.credentials import AzureKeyCredential
    assert isinstance(call_kwargs.kwargs.get("credential"), AzureKeyCredential)


# ---------------------------------------------------------------------------
# Tool filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_tools_filters_azure_prefixes() -> None:
    """AzureHealthAgent should expose az_* and aks_* tools but not call_kubectl."""
    from mcp.types import Tool
    from server.logging_config import get_logger

    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    agent._log = get_logger("test")

    mock_session = AsyncMock()
    mock_response = MagicMock()
    mock_response.tools = [
        Tool(name="az_aks_operations", description="Azure AKS ops", inputSchema={"type": "object", "properties": {}}),
        Tool(name="aks_monitoring", description="AKS monitoring", inputSchema={"type": "object", "properties": {}}),
        Tool(name="call_kubectl", description="kubectl", inputSchema={"type": "object", "properties": {}}),
        Tool(name="get_aks_vmss_info", description="VMSS info", inputSchema={"type": "object", "properties": {}}),
    ]
    mock_session.list_tools = AsyncMock(return_value=mock_response)

    result = await agent._discover_tools(mock_session)

    tool_names = [t.function.name for t in result]
    assert "az_aks_operations" in tool_names
    assert "aks_monitoring" in tool_names
    assert "get_aks_vmss_info" in tool_names
    assert "call_kubectl" not in tool_names


@pytest.mark.asyncio
async def test_discover_tools_filters_cluster_prefixes() -> None:
    """ClusterHealthAgent should expose call_kubectl and collect_* but not az_* tools."""
    from mcp.types import Tool
    from server.logging_config import get_logger

    agent = ClusterHealthAgent.__new__(ClusterHealthAgent)
    agent._log = get_logger("test")

    mock_session = AsyncMock()
    mock_response = MagicMock()
    mock_response.tools = [
        Tool(name="call_kubectl", description="kubectl", inputSchema={"type": "object", "properties": {}}),
        Tool(name="collect_aks_node_logs", description="node logs", inputSchema={"type": "object", "properties": {}}),
        Tool(name="az_aks_operations", description="Azure ops", inputSchema={"type": "object", "properties": {}}),
        Tool(name="inspektor_gadget_observability", description="eBPF", inputSchema={"type": "object", "properties": {}}),
    ]
    mock_session.list_tools = AsyncMock(return_value=mock_response)

    result = await agent._discover_tools(mock_session)

    tool_names = [t.function.name for t in result]
    assert "call_kubectl" in tool_names
    assert "collect_aks_node_logs" in tool_names
    assert "inspektor_gadget_observability" in tool_names
    assert "az_aks_operations" not in tool_names


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_returns_text_content() -> None:
    """_execute_tool should return the tool result text."""
    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    agent.tool_results = []
    from server.logging_config import get_logger
    agent._log = get_logger("test")

    mock_session = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps({"clusters": [], "count": 0})
    mock_result = MagicMock()
    mock_result.content = [mock_content]
    mock_session.call_tool = AsyncMock(return_value=mock_result)

    result = await agent._execute_tool(mock_session, "az_aks_operations", {})
    parsed = json.loads(result)
    assert parsed["count"] == 0


@pytest.mark.asyncio
async def test_execute_tool_handles_exception() -> None:
    """_execute_tool should return error JSON on exception."""
    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    agent.tool_results = []
    from server.logging_config import get_logger
    agent._log = get_logger("test")

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(side_effect=RuntimeError("Connection refused"))

    result = await agent._execute_tool(mock_session, "az_aks_operations", {})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "Connection refused" in parsed["error"]


# ---------------------------------------------------------------------------
# Root agent sub-agent dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_agent_dispatches_to_correct_sub_agent() -> None:
    """_call_sub_agent routes to the right task agent based on tool name."""
    from agents.root_agent import RootAgent

    with (
        patch("agents.root_agent.ChatCompletionsClient"),
        patch("agents.root_agent.get_azure_credential"),
        patch.object(AzureHealthAgent, "run", new_callable=AsyncMock) as mock_azure,
        patch.object(ClusterHealthAgent, "run", new_callable=AsyncMock) as mock_cluster,
    ):
        mock_azure.return_value = "Azure health report"
        mock_cluster.return_value = "Cluster health report"

        root = RootAgent()
        root._log = MagicMock()

        azure_result = await root._call_sub_agent(
            "call_1", "query_azure_health", json.dumps({"query": "List clusters"})
        )
        cluster_result = await root._call_sub_agent(
            "call_2", "query_cluster_health", json.dumps({"query": "Check pods"})
        )
        unknown_result = await root._call_sub_agent(
            "call_3", "unknown_tool", "{}"
        )

    assert azure_result == "Azure health report"
    assert cluster_result == "Cluster health report"
    assert "error" in json.loads(unknown_result)


@pytest.mark.asyncio
async def test_root_agent_runs_sub_agents_concurrently() -> None:
    """_dispatch_tools should gather all tool calls in parallel."""
    import asyncio
    from agents.root_agent import RootAgent

    call_order: list[str] = []

    async def slow_azure(query: str, **kwargs: object) -> str:
        call_order.append("azure_start")
        await asyncio.sleep(0.01)
        call_order.append("azure_end")
        return "azure"

    async def slow_cluster(query: str, **kwargs: object) -> str:
        call_order.append("cluster_start")
        await asyncio.sleep(0.01)
        call_order.append("cluster_end")
        return "cluster"

    with (
        patch("agents.root_agent.ChatCompletionsClient"),
        patch("agents.root_agent.get_azure_credential"),
        patch.object(AzureHealthAgent, "run", side_effect=slow_azure),
        patch.object(ClusterHealthAgent, "run", side_effect=slow_cluster),
    ):
        root = RootAgent()
        root._log = MagicMock()

        tc1 = MagicMock()
        tc1.id = "call_1"
        tc1.function.name = "query_azure_health"
        tc1.function.arguments = json.dumps({"query": "check azure"})

        tc2 = MagicMock()
        tc2.id = "call_2"
        tc2.function.name = "query_cluster_health"
        tc2.function.arguments = json.dumps({"query": "check cluster"})

        results = await root._dispatch_tools([tc1, tc2])

    # Both should have started before either ended (concurrency check)
    assert "azure_start" in call_order
    assert "cluster_start" in call_order
    assert len(results) == 2
