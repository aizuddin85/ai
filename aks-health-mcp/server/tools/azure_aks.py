"""
Azure AKS health tools – all operations are strictly read-only (GET).

Tools exposed:
  - list_aks_clusters
  - get_aks_cluster_detail
  - get_aks_node_pools
  - get_aks_upgrade_profile
  - get_resource_health_events
  - get_aks_metrics

Multi-subscription support
--------------------------
list_aks_clusters and get_resource_health_events automatically iterate over
every subscription in AZURE_SUBSCRIPTION_IDS when no explicit subscription_id
is supplied.  All other tools operate on a single cluster; if more than one
subscription is configured and no explicit subscription_id is given they return
a descriptive error so the caller can retry with an explicit ID.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.resourcehealth import ResourceHealthMgmtClient
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from server.auth.credentials import get_azure_credential
from server.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cs_client(subscription_id: str) -> ContainerServiceClient:
    return ContainerServiceClient(get_azure_credential(), subscription_id)


def _health_client(subscription_id: str) -> ResourceHealthMgmtClient:
    return ResourceHealthMgmtClient(get_azure_credential(), subscription_id)


def _monitor_client(subscription_id: str) -> MonitorManagementClient:
    return MonitorManagementClient(get_azure_credential(), subscription_id)


def _safe_serialize(obj: Any) -> Any:
    """Recursively convert Azure SDK model objects to JSON-safe dicts."""
    if hasattr(obj, "as_dict"):
        return _safe_serialize(obj.as_dict())
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_safe_serialize(i) for i in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _resolve_subscriptions(subscription_id: str | None) -> list[str]:
    """
    Return the list of subscription IDs to query.

    If an explicit subscription_id is provided it is used alone.
    Otherwise all IDs from AZURE_SUBSCRIPTION_IDS are returned.
    Returns an empty list when nothing is configured.
    """
    if subscription_id:
        return [subscription_id]
    return get_settings().subscription_ids


def _resolve_single_subscription(
    subscription_id: str | None,
) -> tuple[str, None] | tuple[None, str]:
    """
    Return (sub_id, None) for tools that operate on a single resource.

    When multiple subscriptions are configured and no explicit ID is given,
    returns (None, error_message) so the caller can surface a clear error.
    """
    if subscription_id:
        return subscription_id, None

    subs = get_settings().subscription_ids
    if not subs:
        return None, "subscription_id is required (AZURE_SUBSCRIPTION_IDS not configured)"
    if len(subs) == 1:
        return subs[0], None

    sub_preview = ", ".join(subs[:3]) + ("…" if len(subs) > 3 else "")
    return None, (
        f"Multiple subscriptions are configured ({sub_preview}). "
        "Provide an explicit subscription_id to identify which subscription "
        "the target cluster belongs to."
    )


@retry(
    retry=retry_if_exception_type(HttpResponseError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _list_clusters_raw(subscription_id: str) -> list[Any]:
    client = _cs_client(subscription_id)
    return list(client.managed_clusters.list())


# ---------------------------------------------------------------------------
# Exposed tool functions
# ---------------------------------------------------------------------------


def list_aks_clusters(
    subscription_id: str | None = None,
    resource_group: str | None = None,
) -> str:
    """
    List AKS clusters across one or more subscriptions.

    When subscription_id is omitted the function iterates every subscription
    in AZURE_SUBSCRIPTION_IDS and aggregates results.  Partial errors from
    individual subscriptions are included in the response rather than
    aborting the entire query.

    Args:
        subscription_id: Azure subscription ID. When blank, all configured
                         subscriptions are queried.
        resource_group:  Optional resource group name to filter results.

    Returns:
        JSON with cluster list, total count, subscriptions queried, and any
        per-subscription errors.
    """
    subs = _resolve_subscriptions(subscription_id)
    if not subs:
        return json.dumps({"error": "subscription_id is required (AZURE_SUBSCRIPTION_IDS not configured)"})

    all_clusters: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for sub in subs:
        log = logger.bind(subscription_id=sub, resource_group=resource_group)
        log.info("tool.list_aks_clusters.start")
        try:
            client = _cs_client(sub)
            if resource_group:
                raw = list(client.managed_clusters.list_by_resource_group(resource_group))
            else:
                raw = _list_clusters_raw(sub)

            for c in raw:
                rg = c.id.split("/")[4] if c.id else None
                all_clusters.append(
                    {
                        "name": c.name,
                        "subscription_id": sub,
                        "resource_group": rg,
                        "location": c.location,
                        "kubernetes_version": c.kubernetes_version,
                        "provisioning_state": c.provisioning_state,
                        "power_state": (
                            c.power_state.code if c.power_state else "Unknown"
                        ),
                        "fqdn": c.fqdn,
                        "node_resource_group": c.node_resource_group,
                        "tags": c.tags or {},
                    }
                )
            log.info("tool.list_aks_clusters.done", count=len(raw))

        except HttpResponseError as exc:
            err_code = exc.error.code if exc.error else str(exc)
            log.warning("tool.list_aks_clusters.error", error=err_code)
            errors.append({"subscription_id": sub, "error": f"Azure API error: {err_code}"})

    result: dict[str, Any] = {
        "clusters": all_clusters,
        "count": len(all_clusters),
        "subscriptions_queried": subs,
    }
    if errors:
        result["errors"] = errors
    return json.dumps(result)


def get_aks_cluster_detail(
    cluster_name: str,
    resource_group: str,
    subscription_id: str | None = None,
) -> str:
    """
    Get detailed health information for a specific AKS cluster.

    Args:
        cluster_name:    Name of the AKS cluster.
        resource_group:  Resource group containing the cluster.
        subscription_id: Azure subscription ID. Required when more than one
                         subscription is configured in AZURE_SUBSCRIPTION_IDS.

    Returns:
        JSON string with cluster details including agent pool profiles,
        network profile, addon profiles, and OIDC/identity settings.
    """
    sub, err = _resolve_single_subscription(subscription_id)
    if err:
        return json.dumps({"error": err})

    log = logger.bind(cluster=cluster_name, rg=resource_group, sub=sub)
    log.info("tool.get_aks_cluster_detail.start")

    try:
        client = _cs_client(sub)  # type: ignore[arg-type]
        cluster = client.managed_clusters.get(resource_group, cluster_name)
        detail = _safe_serialize(cluster)
        log.info("tool.get_aks_cluster_detail.done")
        return json.dumps(detail)

    except ResourceNotFoundError:
        return json.dumps(
            {"error": f"Cluster '{cluster_name}' not found in '{resource_group}'"}
        )
    except HttpResponseError as exc:
        log.warning("tool.get_aks_cluster_detail.error", error=str(exc))
        return json.dumps(
            {"error": f"Azure API error: {exc.error.code if exc.error else str(exc)}"}
        )


def get_aks_node_pools(
    cluster_name: str,
    resource_group: str,
    subscription_id: str | None = None,
) -> str:
    """
    List all node pools and their current status for an AKS cluster.

    Args:
        cluster_name:    Name of the AKS cluster.
        resource_group:  Resource group containing the cluster.
        subscription_id: Azure subscription ID. Required when more than one
                         subscription is configured in AZURE_SUBSCRIPTION_IDS.

    Returns:
        JSON string with node pool names, VM sizes, counts, power states,
        provisioning states, OS types, and Kubernetes version per pool.
    """
    sub, err = _resolve_single_subscription(subscription_id)
    if err:
        return json.dumps({"error": err})

    log = logger.bind(cluster=cluster_name, rg=resource_group)
    log.info("tool.get_aks_node_pools.start")

    try:
        client = _cs_client(sub)  # type: ignore[arg-type]
        pools = list(client.agent_pools.list(resource_group, cluster_name))
        result = []
        for p in pools:
            result.append(
                {
                    "name": p.name,
                    "vm_size": p.vm_size,
                    "count": p.count,
                    "min_count": p.min_count,
                    "max_count": p.max_count,
                    "enable_auto_scaling": p.enable_auto_scaling,
                    "os_type": p.os_type,
                    "os_disk_size_gb": p.os_disk_size_gb,
                    "kubernetes_version": p.kubernetes_version,
                    "provisioning_state": p.provisioning_state,
                    "power_state": (
                        p.power_state.code if p.power_state else "Unknown"
                    ),
                    "node_labels": p.node_labels or {},
                    "node_taints": p.node_taints or [],
                    "mode": p.mode,
                    "upgrade_settings": _safe_serialize(p.upgrade_settings),
                }
            )
        log.info("tool.get_aks_node_pools.done", pool_count=len(result))
        return json.dumps({"node_pools": result, "count": len(result)})

    except ResourceNotFoundError:
        return json.dumps({"error": f"Cluster '{cluster_name}' not found"})
    except HttpResponseError as exc:
        log.warning("tool.get_aks_node_pools.error", error=str(exc))
        return json.dumps(
            {"error": f"Azure API error: {exc.error.code if exc.error else str(exc)}"}
        )


def get_aks_upgrade_profile(
    cluster_name: str,
    resource_group: str,
    subscription_id: str | None = None,
) -> str:
    """
    Retrieve the available Kubernetes upgrade versions for an AKS cluster.

    Args:
        cluster_name:    Name of the AKS cluster.
        resource_group:  Resource group containing the cluster.
        subscription_id: Azure subscription ID. Required when more than one
                         subscription is configured in AZURE_SUBSCRIPTION_IDS.

    Returns:
        JSON string with available control-plane and agent pool upgrades,
        including whether each version is in preview.
    """
    sub, err = _resolve_single_subscription(subscription_id)
    if err:
        return json.dumps({"error": err})

    log = logger.bind(cluster=cluster_name, rg=resource_group)
    log.info("tool.get_aks_upgrade_profile.start")

    try:
        client = _cs_client(sub)  # type: ignore[arg-type]
        profile = client.managed_clusters.get_upgrade_profile(resource_group, cluster_name)
        result = {
            "control_plane": {
                "kubernetes_version": profile.control_plane_profile.kubernetes_version,
                "upgrades": [
                    {
                        "kubernetes_version": u.kubernetes_version,
                        "is_preview": u.is_preview,
                    }
                    for u in (profile.control_plane_profile.upgrades or [])
                ],
            },
            "agent_pools": [
                {
                    "name": ap.name,
                    "kubernetes_version": ap.kubernetes_version,
                    "upgrades": [
                        {
                            "kubernetes_version": u.kubernetes_version,
                            "is_preview": u.is_preview,
                        }
                        for u in (ap.upgrades or [])
                    ],
                }
                for ap in (profile.agent_pool_profiles or [])
            ],
        }
        log.info("tool.get_aks_upgrade_profile.done")
        return json.dumps(result)

    except ResourceNotFoundError:
        return json.dumps({"error": f"Cluster '{cluster_name}' not found"})
    except HttpResponseError as exc:
        log.warning("tool.get_aks_upgrade_profile.error", error=str(exc))
        return json.dumps(
            {"error": f"Azure API error: {exc.error.code if exc.error else str(exc)}"}
        )


def get_resource_health_events(
    subscription_id: str | None = None,
    resource_group: str | None = None,
    cluster_name: str | None = None,
) -> str:
    """
    Retrieve Azure Resource Health events across one or more subscriptions.

    When subscription_id is omitted the function iterates every subscription
    in AZURE_SUBSCRIPTION_IDS.  Partial errors from individual subscriptions
    are included in the response rather than aborting the entire query.

    Args:
        subscription_id: Azure subscription ID. When blank, all configured
                         subscriptions are queried.
        resource_group:  Optional – scope to a specific resource group.
        cluster_name:    Optional – scope to a specific AKS cluster
                         (requires resource_group).

    Returns:
        JSON string with health events including event type, reason,
        impact start/end time, and current health status.
    """
    subs = _resolve_subscriptions(subscription_id)
    if not subs:
        return json.dumps({"error": "subscription_id is required (AZURE_SUBSCRIPTION_IDS not configured)"})

    all_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for sub in subs:
        log = logger.bind(sub=sub, rg=resource_group, cluster=cluster_name)
        log.info("tool.get_resource_health_events.start")

        try:
            client = _health_client(sub)
            events_iter = client.events.list_by_subscription_id()

            for ev in events_iter:
                ev_dict: dict[str, Any] = {}
                try:
                    ev_dict = ev.as_dict() if hasattr(ev, "as_dict") else {}
                except Exception:  # noqa: BLE001
                    pass

                # Filter to AKS managed clusters when cluster scope requested
                if resource_group and cluster_name:
                    ev_id = ev_dict.get("id", "")
                    scope = (
                        f"/subscriptions/{sub}/resourceGroups/{resource_group}"
                        f"/providers/Microsoft.ContainerService/managedClusters/{cluster_name}"
                    ).lower()
                    if scope not in ev_id.lower():
                        continue

                all_events.append(
                    {
                        "subscription_id": sub,
                        "id": ev_dict.get("id"),
                        "name": ev_dict.get("name"),
                        "event_type": ev_dict.get("properties", {}).get("eventType"),
                        "event_source": ev_dict.get("properties", {}).get("eventSource"),
                        "status": ev_dict.get("properties", {}).get("status", {}).get("value")
                        if isinstance(ev_dict.get("properties", {}).get("status"), dict)
                        else ev_dict.get("properties", {}).get("status"),
                        "title": ev_dict.get("properties", {}).get("title"),
                        "summary": ev_dict.get("properties", {}).get("summary"),
                        "impact_start_time": ev_dict.get("properties", {}).get(
                            "impactStartTime"
                        ),
                        "impact_mitigation_time": ev_dict.get("properties", {}).get(
                            "impactMitigationTime"
                        ),
                        "level": ev_dict.get("properties", {}).get("level"),
                    }
                )

            log.info("tool.get_resource_health_events.done", event_count=len(all_events))

        except HttpResponseError as exc:
            err_code = exc.error.code if exc.error else str(exc)
            log.warning("tool.get_resource_health_events.error", error=err_code)
            errors.append(
                {
                    "subscription_id": sub,
                    "error": f"Azure API error: {err_code}",
                    "note": "Resource Health Events API may require preview features enabled on the subscription.",
                }
            )

    result: dict[str, Any] = {
        "events": all_events,
        "count": len(all_events),
        "subscriptions_queried": subs,
    }
    if errors:
        result["errors"] = errors
    return json.dumps(result)


def get_aks_metrics(
    cluster_name: str,
    resource_group: str,
    metric_names: list[str],
    subscription_id: str | None = None,
    timespan_hours: int = 1,
) -> str:
    """
    Query Azure Monitor metrics for an AKS cluster.

    Args:
        cluster_name:    Name of the AKS cluster.
        resource_group:  Resource group containing the cluster.
        metric_names:    List of metric names, e.g.:
                         ["node_cpu_usage_percentage",
                          "node_memory_rss_percentage"]
        subscription_id: Azure subscription ID. Required when more than one
                         subscription is configured in AZURE_SUBSCRIPTION_IDS.
        timespan_hours:  How many hours back to query (default: 1).

    Common AKS metric names:
        node_cpu_usage_percentage
        node_memory_rss_percentage
        node_memory_working_set_percentage
        kube_node_status_allocatable_cpu_cores
        kube_node_status_allocatable_memory_bytes
        kube_pod_status_ready

    Returns:
        JSON string with metric time-series data.
    """
    sub, err = _resolve_single_subscription(subscription_id)
    if err:
        return json.dumps({"error": err})

    resource_uri = (
        f"/subscriptions/{sub}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.ContainerService/managedClusters/{cluster_name}"
    )
    log = logger.bind(resource_uri=resource_uri, metrics=metric_names)
    log.info("tool.get_aks_metrics.start")

    try:
        client = _monitor_client(sub)  # type: ignore[arg-type]
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(hours=timespan_hours)
        timespan = f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}"

        response = client.metrics.list(
            resource_uri=resource_uri,
            timespan=timespan,
            interval="PT5M",
            metricnames=",".join(metric_names),
            aggregation="average,maximum,minimum",
            metricnamespace="Microsoft.ContainerService/managedClusters",
        )

        result = []
        for metric in response.value or []:
            series = []
            for ts in metric.timeseries or []:
                data_points = []
                for dp in ts.data or []:
                    if any(
                        v is not None
                        for v in [dp.average, dp.maximum, dp.minimum]
                    ):
                        data_points.append(
                            {
                                "timestamp": dp.time_stamp.isoformat()
                                if dp.time_stamp
                                else None,
                                "average": dp.average,
                                "maximum": dp.maximum,
                                "minimum": dp.minimum,
                            }
                        )
                if data_points:
                    meta = {
                        kv.name.value: kv.value
                        for kv in (ts.metadata_values or [])
                        if kv.name
                    }
                    series.append({"metadata": meta, "data": data_points})
            result.append(
                {
                    "name": metric.name.value if metric.name else None,
                    "display_description": metric.display_description,
                    "unit": str(metric.unit) if metric.unit else None,
                    "timeseries": series,
                }
            )

        log.info("tool.get_aks_metrics.done", metric_count=len(result))
        return json.dumps(
            {
                "cluster": cluster_name,
                "resource_group": resource_group,
                "subscription_id": sub,
                "timespan_hours": timespan_hours,
                "metrics": result,
            }
        )

    except HttpResponseError as exc:
        log.warning("tool.get_aks_metrics.error", error=str(exc))
        return json.dumps(
            {
                "error": f"Azure Monitor error: {exc.error.code if exc.error else str(exc)}"
            }
        )
