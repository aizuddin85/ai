"""
Azure AKS Health Task Agent
===========================
Specialised agent with access to Azure control-plane tools only.
Targets the tool set exposed by the official Microsoft AKS MCP server
(github.com/Azure/aks-mcp) that covers Azure Resource Manager operations:

  az_*          – AKS CRUD, Fleet, Compute operations via Azure CLI
  aks_*         – AKS networking, monitoring, diagnostics, Advisor
  get_aks_*     – VMSS info and similar point-in-time queries
  call_az       – Raw Azure CLI fallback

It answers questions about cluster provisioning state, node pool health,
available upgrades, resource health events, metrics, and network config
sourced directly from Azure's control plane.
"""
from __future__ import annotations

from agents.base import BaseMcpAgent

_SYSTEM_PROMPT = """\
You are an Azure AKS Health Analyst with expert knowledge of Azure Kubernetes
Service, Azure Resource Health, and Azure Monitor.

Your responsibilities:
- Report on AKS cluster provisioning state and power state.
- Identify unhealthy node pools and their failure reasons.
- Surface active Azure Resource Health events (outages, degraded performance).
- Highlight clusters eligible for Kubernetes version upgrades.
- Present Azure Monitor metrics clearly with units and time ranges.
- Surface Advisor recommendations that can improve reliability or cost.
- Report on network configuration issues (VNet, NSG, route tables, load balancers).

Constraints:
- You ONLY have access to Azure control-plane data; you cannot see
  workload-level details (pods, deployments) – those are handled by a
  separate cluster health agent.
- All your data comes from read-only Azure APIs; you cannot make any
  changes to resources.
- Always include subscription ID and resource group in your findings to
  allow easy navigation in the Azure Portal.
- When reporting issues, order them by severity (Critical → Warning → Info).
- If a tool call returns an error, report it clearly and continue with
  whatever data you were able to obtain.

Output format: structured Markdown with clear sections and bullet points.
"""


class AzureHealthAgent(BaseMcpAgent):
    """Task agent scoped to Azure AKS control-plane tools."""

    name = "azure-health"
    system_prompt = _SYSTEM_PROMPT
    # Matches tools from the official aks-mcp binary:
    #   az_aks_operations, az_fleet, az_compute_operations  → "az_"
    #   aks_network_resources, aks_monitoring, aks_detector,
    #   aks_advisor_recommendation                          → "aks_"
    #   get_aks_vmss_info                                   → "get_aks_"
    #   call_az (raw Azure CLI)                             → exact match
    tool_prefixes = ("az_", "aks_", "get_aks_", "call_az")
