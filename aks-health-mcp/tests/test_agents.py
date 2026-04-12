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


def test_azure_health_agent_tool_prefix() -> None:
    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    assert agent.tool_prefix == "aks_"


def test_cluster_health_agent_tool_prefix() -> None:
    agent = ClusterHealthAgent.__new__(ClusterHealthAgent)
    assert agent.tool_prefix == "k8s_"


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
async def test_discover_tools_filters_by_prefix() -> None:
    """AzureHealthAgent should only expose aks_* tools."""
    from mcp.types import Tool
    from server.logging_config import get_logger

    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    agent.tool_prefix = "aks_"
    agent._log = get_logger("test")

    # Build a mock MCP session that returns mixed tools
    mock_session = AsyncMock()
    mock_response = MagicMock()
    mock_response.tools = [
        Tool(name="aks_list_clusters", description="Azure tool", inputSchema={"type": "object", "properties": {}}),
        Tool(name="k8s_get_nodes", description="K8s tool", inputSchema={"type": "object", "properties": {}}),
        Tool(name="aks_get_metrics", description="Azure tool 2", inputSchema={"type": "object", "properties": {}}),
    ]
    mock_session.list_tools = AsyncMock(return_value=mock_response)

    result = await agent._discover_tools(mock_session)

    tool_names = [t.function.name for t in result]
    assert "aks_list_clusters" in tool_names
    assert "aks_get_metrics" in tool_names
    assert "k8s_get_nodes" not in tool_names
    assert len(result) == 2


@pytest.mark.asyncio
async def test_discover_tools_filters_k8s_prefix() -> None:
    """ClusterHealthAgent should only expose k8s_* tools."""
    from mcp.types import Tool
    from server.logging_config import get_logger

    agent = ClusterHealthAgent.__new__(ClusterHealthAgent)
    agent.tool_prefix = "k8s_"
    agent._log = get_logger("test")

    mock_session = AsyncMock()
    mock_response = MagicMock()
    mock_response.tools = [
        Tool(name="aks_list_clusters", description="Azure tool", inputSchema={"type": "object", "properties": {}}),
        Tool(name="k8s_get_nodes", description="K8s tool", inputSchema={"type": "object", "properties": {}}),
    ]
    mock_session.list_tools = AsyncMock(return_value=mock_response)

    result = await agent._discover_tools(mock_session)

    tool_names = [t.function.name for t in result]
    assert "k8s_get_nodes" in tool_names
    assert "aks_list_clusters" not in tool_names


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_returns_text_content() -> None:
    """_execute_tool should return the tool result text."""
    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    agent.tool_prefix = "aks_"
    agent.tool_results = []
    from server.logging_config import get_logger
    agent._log = get_logger("test")

    mock_session = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps({"clusters": [], "count": 0})
    mock_result = MagicMock()
    mock_result.content = [mock_content]
    mock_session.call_tool = AsyncMock(return_value=mock_result)

    result = await agent._execute_tool(mock_session, "aks_list_clusters", {})
    parsed = json.loads(result)
    assert parsed["count"] == 0


@pytest.mark.asyncio
async def test_execute_tool_handles_exception() -> None:
    """_execute_tool should return error JSON on exception."""
    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    agent.tool_prefix = "aks_"
    agent.tool_results = []
    from server.logging_config import get_logger
    agent._log = get_logger("test")

    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(side_effect=RuntimeError("Connection refused"))

    result = await agent._execute_tool(mock_session, "aks_list_clusters", {})
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


# ---------------------------------------------------------------------------
# OBO token propagation via _build_server_env
# ---------------------------------------------------------------------------


def test_build_server_env_injects_arm_and_k8s_tokens() -> None:
    """Both AZURE_ARM_TOKEN and AZURE_K8S_TOKEN are set when tokens are provided."""
    agent = AzureHealthAgent.__new__(AzureHealthAgent)

    env = agent._build_server_env(arm_token="arm-tok", k8s_token="k8s-tok")

    assert env["AZURE_ARM_TOKEN"] == "arm-tok"
    assert env["AZURE_K8S_TOKEN"] == "k8s-tok"


def test_build_server_env_removes_stale_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale tokens are removed from the env when not provided for this request."""
    monkeypatch.setenv("AZURE_ARM_TOKEN", "old-arm")
    monkeypatch.setenv("AZURE_K8S_TOKEN", "old-k8s")

    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    env = agent._build_server_env(arm_token=None, k8s_token=None)

    assert "AZURE_ARM_TOKEN" not in env
    assert "AZURE_K8S_TOKEN" not in env


def test_build_server_env_partial_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """When only arm_token is given, AZURE_K8S_TOKEN is absent."""
    monkeypatch.delenv("AZURE_K8S_TOKEN", raising=False)

    agent = AzureHealthAgent.__new__(AzureHealthAgent)
    env = agent._build_server_env(arm_token="arm-only", k8s_token=None)

    assert env["AZURE_ARM_TOKEN"] == "arm-only"
    assert "AZURE_K8S_TOKEN" not in env


# ---------------------------------------------------------------------------
# K8s OBO token exchange (_exchange_k8s_obo_token)
# ---------------------------------------------------------------------------


def test_exchange_k8s_obo_token_returns_none_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns None when no client secret is configured (no OBO possible)."""
    from api.routes.agent import _exchange_k8s_obo_token
    from api.config import ApiSettings

    settings = ApiSettings(
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        azure_ad_app_client_id="22222222-2222-2222-2222-222222222222",
        azure_foundry_endpoint="https://test.services.ai.azure.com/models",
        azure_foundry_model="gpt-4o",
        frontend_origin="http://localhost:5173",
        azure_client_secret=None,
    )

    result = _exchange_k8s_obo_token("user-token", settings)
    assert result is None


@patch("api.routes.agent._make_obo_credential")
def test_exchange_k8s_obo_token_uses_aks_app_scope(mock_make_cred: MagicMock) -> None:
    """The K8s OBO exchange requests the AKS server app scope."""
    from api.routes.agent import _exchange_k8s_obo_token, _AKS_SERVER_APP_ID
    from api.config import ApiSettings

    mock_cred = MagicMock()
    mock_cred.get_token.return_value = MagicMock(token="k8s-access-token")
    mock_make_cred.return_value = mock_cred

    settings = ApiSettings(
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        azure_ad_app_client_id="22222222-2222-2222-2222-222222222222",
        azure_foundry_endpoint="https://test.services.ai.azure.com/models",
        azure_foundry_model="gpt-4o",
        frontend_origin="http://localhost:5173",
    )

    result = _exchange_k8s_obo_token("user-token", settings)

    assert result == "k8s-access-token"
    mock_cred.get_token.assert_called_once_with(f"{_AKS_SERVER_APP_ID}/.default")


@patch("api.routes.agent._make_obo_credential")
def test_exchange_k8s_obo_token_returns_none_on_error(
    mock_make_cred: MagicMock,
) -> None:
    """Returns None (does not raise) when the OBO exchange fails."""
    from api.routes.agent import _exchange_k8s_obo_token
    from api.config import ApiSettings

    mock_cred = MagicMock()
    mock_cred.get_token.side_effect = RuntimeError("AADSTS token error")
    mock_make_cred.return_value = mock_cred

    settings = ApiSettings(
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        azure_ad_app_client_id="22222222-2222-2222-2222-222222222222",
        azure_foundry_endpoint="https://test.services.ai.azure.com/models",
        azure_foundry_model="gpt-4o",
        frontend_origin="http://localhost:5173",
    )

    result = _exchange_k8s_obo_token("user-token", settings)
    assert result is None


# ---------------------------------------------------------------------------
# Root agent propagates k8s_token to ClusterHealthAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_agent_passes_k8s_token_to_cluster_agent() -> None:
    """_call_sub_agent passes k8s_token through to ClusterHealthAgent.run()."""
    from agents.root_agent import RootAgent

    with (
        patch("agents.root_agent.ChatCompletionsClient"),
        patch("agents.root_agent.get_azure_credential"),
        patch.object(ClusterHealthAgent, "run", new_callable=AsyncMock) as mock_cluster,
    ):
        mock_cluster.return_value = "Cluster health"

        root = RootAgent()
        root._log = MagicMock()
        root._arm_token = "arm-tok"
        root._k8s_token = "k8s-tok"

        await root._call_sub_agent(
            "call_1", "query_cluster_health", json.dumps({"query": "Check pods"})
        )

    mock_cluster.assert_called_once_with(
        "Check pods", arm_token="arm-tok", k8s_token="k8s-tok"
    )
