"""
Integration-style tests for the MCP server tool registration.
Verifies that all expected tools are registered and have the correct
name, description, and basic schema shape without needing real Azure/K8s.
"""
from __future__ import annotations

import asyncio

import pytest


def _get_tools() -> list:
    """Fetch tools from the FastMCP instance synchronously."""
    from server.main import mcp
    return asyncio.run(mcp.list_tools())


def test_mcp_server_has_azure_tools() -> None:
    """All Azure AKS tools should be registered on the FastMCP server."""
    tool_names = {t.name for t in _get_tools()}
    expected_azure_tools = {
        "aks_list_clusters",
        "aks_get_cluster_detail",
        "aks_get_node_pools",
        "aks_get_upgrade_profile",
        "aks_get_resource_health_events",
        "aks_get_metrics",
    }
    assert expected_azure_tools.issubset(tool_names), (
        f"Missing Azure tools: {expected_azure_tools - tool_names}"
    )


def test_mcp_server_has_k8s_tools() -> None:
    """All Kubernetes tools should be registered on the FastMCP server."""
    tool_names = {t.name for t in _get_tools()}
    expected_k8s_tools = {
        "k8s_get_nodes",
        "k8s_get_pods",
        "k8s_get_deployments",
        "k8s_get_daemonsets",
        "k8s_get_statefulsets",
        "k8s_get_events",
        "k8s_get_namespaces",
        "k8s_get_pvc_status",
        "k8s_get_services",
        "k8s_get_component_status",
    }
    assert expected_k8s_tools.issubset(tool_names), (
        f"Missing K8s tools: {expected_k8s_tools - tool_names}"
    )


def test_all_tools_have_descriptions() -> None:
    """Every registered tool must have a non-empty description."""
    for tool in _get_tools():
        assert tool.description, f"Tool '{tool.name}' is missing a description"


def test_no_write_tools_registered() -> None:
    """
    Verify read-only contract: no tool names should suggest mutation
    (create, update, delete, patch, scale, restart, etc.).
    """
    import re

    # Match whole tokens separated by underscores, not substrings
    write_verbs = {"create", "update", "delete", "patch", "scale", "restart", "apply"}
    tool_names = {t.name for t in _get_tools()}

    violating = [
        name
        for name in tool_names
        if any(re.search(rf"(^|_){verb}($|_)", name.lower()) for verb in write_verbs)
    ]
    assert not violating, f"Potentially mutating tools found: {violating}"


def test_tool_count() -> None:
    """There should be exactly 16 tools (6 Azure + 10 K8s)."""
    tools = _get_tools()
    assert len(tools) == 16, f"Expected 16 tools, got {len(tools)}: {[t.name for t in tools]}"
