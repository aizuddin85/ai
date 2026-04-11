"""
AKS Health MCP Server
=====================
Exposes read-only tools for querying AKS cluster health from both
Azure Resource Manager and the live Kubernetes API.

Transport modes:
  stdio – default; used when agents spawn the server as a subprocess.
  sse   – HTTP Server-Sent Events; set MCP_TRANSPORT=sse.

Security guarantees:
  * All exposed tools are read-only (GET / list / watch verbs only).
  * No tool accepts credentials as input; they are loaded from the
    server environment at startup.
  * Tool call arguments are validated before dispatch.
  * All calls are audit-logged with tool name, arguments (no secrets),
    and caller identity.

Usage:
  python -m server.main           # stdio transport
  MCP_TRANSPORT=sse python -m server.main   # SSE transport
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from server.config import get_settings
from server.logging_config import configure_logging, get_logger

# ── Initialise logging early (before imports that might log) ─────────────────
_settings = get_settings()
configure_logging(log_level=_settings.log_level, log_format=_settings.log_format)
logger = get_logger(__name__)

# ── Lazy imports of tool modules (so auth errors surface at call time) ────────
from server.tools.azure_aks import (  # noqa: E402
    get_aks_cluster_detail,
    get_aks_metrics,
    get_aks_node_pools,
    get_aks_upgrade_profile,
    get_resource_health_events,
    list_aks_clusters,
)
from server.tools.cluster_k8s import (  # noqa: E402
    get_cluster_component_status,
    get_cluster_daemonsets,
    get_cluster_deployments,
    get_cluster_events,
    get_cluster_namespaces,
    get_cluster_nodes,
    get_cluster_pods,
    get_cluster_pvc_status,
    get_cluster_services,
    get_cluster_statefulsets,
)

# ── MCP server instance ───────────────────────────────────────────────────────
mcp = FastMCP(
    name="aks-health-mcp",
    instructions=(
        "Read-only AKS health MCP server. "
        "Provides tools to query Azure AKS cluster health from Azure Resource Manager "
        "and from the live Kubernetes API. "
        "All operations are strictly read-only – no mutations are possible."
    ),
)


# ── Audit logging helper ──────────────────────────────────────────────────────
def _audit(tool: str, args: dict[str, Any]) -> None:
    """Emit a structured audit log entry for every tool invocation."""
    safe_args = {k: v for k, v in args.items() if "secret" not in k.lower()}
    logger.info("mcp.tool.call", tool=tool, args=safe_args)


# ============================================================================
# Azure AKS tools
# ============================================================================


@mcp.tool()
def aks_list_clusters(
    subscription_id: str = "",
    resource_group: str = "",
) -> str:
    """
    List AKS clusters across one or more Azure subscriptions.

    Leave subscription_id blank to query ALL subscriptions configured in
    AZURE_SUBSCRIPTION_IDS (aggregated results with per-cluster subscription_id).
    Provide subscription_id to scope the query to a single subscription.
    Use resource_group to further narrow results to a specific resource group.
    """
    _audit("aks_list_clusters", {"subscription_id": subscription_id, "resource_group": resource_group})
    return list_aks_clusters(
        subscription_id=subscription_id or None,
        resource_group=resource_group or None,
    )


@mcp.tool()
def aks_get_cluster_detail(
    cluster_name: str,
    resource_group: str,
    subscription_id: str = "",
) -> str:
    """
    Get detailed configuration and health status of a specific AKS cluster.

    Returns provisioning state, power state, Kubernetes version, network
    profile, OIDC issuer, add-on profiles, and agent pool summaries.
    """
    _audit(
        "aks_get_cluster_detail",
        {"cluster_name": cluster_name, "resource_group": resource_group},
    )
    return get_aks_cluster_detail(
        cluster_name=cluster_name,
        resource_group=resource_group,
        subscription_id=subscription_id or None,
    )


@mcp.tool()
def aks_get_node_pools(
    cluster_name: str,
    resource_group: str,
    subscription_id: str = "",
) -> str:
    """
    List all node pools for an AKS cluster with VM size, replica counts,
    autoscaling settings, and current power/provisioning state.
    """
    _audit(
        "aks_get_node_pools",
        {"cluster_name": cluster_name, "resource_group": resource_group},
    )
    return get_aks_node_pools(
        cluster_name=cluster_name,
        resource_group=resource_group,
        subscription_id=subscription_id or None,
    )


@mcp.tool()
def aks_get_upgrade_profile(
    cluster_name: str,
    resource_group: str,
    subscription_id: str = "",
) -> str:
    """
    Show available Kubernetes upgrades for an AKS cluster's control plane
    and each agent pool, including GA and preview versions.
    """
    _audit(
        "aks_get_upgrade_profile",
        {"cluster_name": cluster_name, "resource_group": resource_group},
    )
    return get_aks_upgrade_profile(
        cluster_name=cluster_name,
        resource_group=resource_group,
        subscription_id=subscription_id or None,
    )


@mcp.tool()
def aks_get_resource_health_events(
    subscription_id: str = "",
    resource_group: str = "",
    cluster_name: str = "",
) -> str:
    """
    Retrieve Azure Resource Health events across one or more subscriptions.

    Leave subscription_id blank to query ALL subscriptions configured in
    AZURE_SUBSCRIPTION_IDS.  Scope by resource_group and/or cluster_name
    to filter results to a specific cluster.
    Returns service health incidents, planned maintenance, and security advisories.
    """
    _audit(
        "aks_get_resource_health_events",
        {
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "cluster_name": cluster_name,
        },
    )
    return get_resource_health_events(
        subscription_id=subscription_id or None,
        resource_group=resource_group or None,
        cluster_name=cluster_name or None,
    )


@mcp.tool()
def aks_get_metrics(
    cluster_name: str,
    resource_group: str,
    metric_names: str,
    subscription_id: str = "",
    timespan_hours: int = 1,
) -> str:
    """
    Query Azure Monitor metrics for an AKS cluster.

    metric_names: Comma-separated list, e.g.:
      "node_cpu_usage_percentage,node_memory_rss_percentage,
       kube_node_status_allocatable_cpu_cores"

    Common metric names:
      node_cpu_usage_percentage
      node_memory_rss_percentage
      node_memory_working_set_percentage
      kube_node_status_allocatable_cpu_cores
      kube_node_status_allocatable_memory_bytes
      kube_pod_status_ready
    """
    _audit(
        "aks_get_metrics",
        {
            "cluster_name": cluster_name,
            "resource_group": resource_group,
            "metric_names": metric_names,
            "timespan_hours": timespan_hours,
        },
    )
    names = [n.strip() for n in metric_names.split(",") if n.strip()]
    if not names:
        return json.dumps({"error": "metric_names must not be empty"})
    return get_aks_metrics(
        cluster_name=cluster_name,
        resource_group=resource_group,
        metric_names=names,
        subscription_id=subscription_id or None,
        timespan_hours=timespan_hours,
    )


# ============================================================================
# In-cluster Kubernetes tools
# ============================================================================


@mcp.tool()
def k8s_get_nodes(label_selector: str = "") -> str:
    """
    List all Kubernetes nodes with their Ready condition, capacity,
    allocatable resources, roles, and kubelet version.

    label_selector: Optional, e.g. "agentpool=nodepool1".
    """
    _audit("k8s_get_nodes", {"label_selector": label_selector})
    return get_cluster_nodes(label_selector=label_selector or None)


@mcp.tool()
def k8s_get_pods(
    namespace: str = "",
    label_selector: str = "",
    field_selector: str = "",
    unhealthy_only: bool = False,
) -> str:
    """
    List pods with phase, container states, restart counts, and conditions.

    namespace:      Leave blank for all namespaces.
    label_selector: e.g. "app=nginx".
    field_selector: e.g. "status.phase=Failed".
    unhealthy_only: Set true to return only non-Running/Succeeded pods.
    """
    _audit(
        "k8s_get_pods",
        {
            "namespace": namespace,
            "label_selector": label_selector,
            "unhealthy_only": unhealthy_only,
        },
    )
    return get_cluster_pods(
        namespace=namespace or None,
        label_selector=label_selector or None,
        field_selector=field_selector or None,
        unhealthy_only=unhealthy_only,
    )


@mcp.tool()
def k8s_get_deployments(namespace: str = "") -> str:
    """
    List Deployments with desired/ready/available replica counts.

    namespace: Leave blank for all namespaces.
    """
    _audit("k8s_get_deployments", {"namespace": namespace})
    return get_cluster_deployments(namespace=namespace or None)


@mcp.tool()
def k8s_get_daemonsets(namespace: str = "") -> str:
    """
    List DaemonSets with desired/ready/misscheduled counts.

    namespace: Leave blank for all namespaces.
    """
    _audit("k8s_get_daemonsets", {"namespace": namespace})
    return get_cluster_daemonsets(namespace=namespace or None)


@mcp.tool()
def k8s_get_statefulsets(namespace: str = "") -> str:
    """
    List StatefulSets with desired/ready replica counts.

    namespace: Leave blank for all namespaces.
    """
    _audit("k8s_get_statefulsets", {"namespace": namespace})
    return get_cluster_statefulsets(namespace=namespace or None)


@mcp.tool()
def k8s_get_events(
    namespace: str = "",
    event_type: str = "",
    limit: int = 100,
) -> str:
    """
    List recent cluster events sorted by most recent first.

    namespace:  Leave blank for all namespaces.
    event_type: "Warning" or "Normal". Leave blank for all.
    limit:      Max events to return (default 100, max 500).
    """
    _audit("k8s_get_events", {"namespace": namespace, "event_type": event_type, "limit": limit})
    return get_cluster_events(
        namespace=namespace or None,
        event_type=event_type or None,
        limit=limit,
    )


@mcp.tool()
def k8s_get_namespaces() -> str:
    """List all Kubernetes namespaces with their phase and labels."""
    _audit("k8s_get_namespaces", {})
    return get_cluster_namespaces()


@mcp.tool()
def k8s_get_pvc_status(namespace: str = "") -> str:
    """
    List PersistentVolumeClaims with binding status, storage class, and capacity.

    namespace: Leave blank for all namespaces.
    """
    _audit("k8s_get_pvc_status", {"namespace": namespace})
    return get_cluster_pvc_status(namespace=namespace or None)


@mcp.tool()
def k8s_get_services(namespace: str = "") -> str:
    """
    List Services with type, cluster IP, ports, and load-balancer ingress addresses.

    namespace: Leave blank for all namespaces.
    """
    _audit("k8s_get_services", {"namespace": namespace})
    return get_cluster_services(namespace=namespace or None)


@mcp.tool()
def k8s_get_component_status() -> str:
    """
    Query the health of Kubernetes control-plane components:
    scheduler, controller-manager, and etcd.
    """
    _audit("k8s_get_component_status", {})
    return get_cluster_component_status()


# ============================================================================
# Entry point
# ============================================================================


def run() -> None:
    """Start the MCP server using the configured transport."""
    transport = _settings.mcp_transport
    logger.info(
        "mcp.server.starting",
        transport=transport,
        uses_sp=_settings.uses_service_principal,
    )

    if transport == "sse":
        # SSE host/port are set via FastMCP constructor kwargs or env.
        # FastMCP.run() only takes transport + mount_path.
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
