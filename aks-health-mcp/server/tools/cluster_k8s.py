"""
In-cluster Kubernetes health tools – all operations use list/get verbs only.

Tools exposed:
  - get_cluster_nodes
  - get_cluster_pods
  - get_cluster_deployments
  - get_cluster_daemonsets
  - get_cluster_statefulsets
  - get_cluster_events
  - get_cluster_namespaces
  - get_cluster_pvc_status
  - get_cluster_services
  - get_cluster_component_status
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from kubernetes.client.exceptions import ApiException
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from server.auth.credentials import get_kubernetes_client

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_NAMESPACES = ""  # empty string means all namespaces in k8s client


def _dt(obj: datetime | None) -> str | None:
    return obj.isoformat() if obj else None


def _safe_meta(obj: Any) -> dict[str, Any]:
    """Extract common ObjectMeta fields."""
    meta = obj.metadata
    return {
        "name": meta.name,
        "namespace": meta.namespace,
        "labels": meta.labels or {},
        "annotations": {
            k: v
            for k, v in (meta.annotations or {}).items()
            if not k.startswith("kubectl.kubernetes.io/last-applied")
        },
        "creation_timestamp": _dt(meta.creation_timestamp),
        "resource_version": meta.resource_version,
        "uid": meta.uid,
    }


def _condition_list(conditions: list[Any] | None) -> list[dict[str, Any]]:
    if not conditions:
        return []
    return [
        {
            "type": c.type,
            "status": c.status,
            "reason": getattr(c, "reason", None),
            "message": getattr(c, "message", None),
            "last_transition_time": _dt(getattr(c, "last_transition_time", None)),
        }
        for c in conditions
    ]


@retry(
    retry=retry_if_exception_type(ApiException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _list_nodes() -> Any:
    core, _ = get_kubernetes_client()
    return core.list_node()


# ---------------------------------------------------------------------------
# Exposed tool functions
# ---------------------------------------------------------------------------


def get_cluster_nodes(
    label_selector: str | None = None,
) -> str:
    """
    List all nodes in the cluster with their conditions and capacity.

    Args:
        label_selector: Optional Kubernetes label selector, e.g. "agentpool=system".

    Returns:
        JSON string with node names, roles, status conditions, capacity,
        allocatable resources, Kubernetes version, and OS image.
    """
    log = logger.bind(label_selector=label_selector)
    log.info("tool.get_cluster_nodes.start")

    try:
        core, _ = get_kubernetes_client()
        kwargs: dict[str, Any] = {}
        if label_selector:
            kwargs["label_selector"] = label_selector

        node_list = core.list_node(**kwargs)
        nodes = []
        for node in node_list.items:
            # Determine roles from labels
            roles = [
                label.replace("node-role.kubernetes.io/", "")
                for label in (node.metadata.labels or {})
                if label.startswith("node-role.kubernetes.io/")
            ]

            # Ready condition
            ready = next(
                (c.status for c in (node.status.conditions or []) if c.type == "Ready"),
                "Unknown",
            )

            nodes.append(
                {
                    **_safe_meta(node),
                    "roles": roles,
                    "ready": ready,
                    "conditions": _condition_list(node.status.conditions),
                    "capacity": {
                        "cpu": node.status.capacity.get("cpu"),
                        "memory": node.status.capacity.get("memory"),
                        "pods": node.status.capacity.get("pods"),
                    }
                    if node.status.capacity
                    else {},
                    "allocatable": {
                        "cpu": node.status.allocatable.get("cpu"),
                        "memory": node.status.allocatable.get("memory"),
                        "pods": node.status.allocatable.get("pods"),
                    }
                    if node.status.allocatable
                    else {},
                    "node_info": {
                        "kubernetes_version": node.status.node_info.kubelet_version
                        if node.status.node_info
                        else None,
                        "os_image": node.status.node_info.os_image
                        if node.status.node_info
                        else None,
                        "container_runtime": node.status.node_info.container_runtime_version
                        if node.status.node_info
                        else None,
                        "kernel_version": node.status.node_info.kernel_version
                        if node.status.node_info
                        else None,
                    },
                    "unschedulable": node.spec.unschedulable or False,
                }
            )

        not_ready = [n["name"] for n in nodes if n["ready"] != "True"]
        log.info("tool.get_cluster_nodes.done", total=len(nodes), not_ready=not_ready)
        return json.dumps({"nodes": nodes, "count": len(nodes), "not_ready": not_ready})

    except ApiException as exc:
        log.warning("tool.get_cluster_nodes.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_pods(
    namespace: str | None = None,
    label_selector: str | None = None,
    field_selector: str | None = None,
    unhealthy_only: bool = False,
) -> str:
    """
    List pods across the cluster with their phase and container statuses.

    Args:
        namespace:      Namespace to query; omit for all namespaces.
        label_selector: Label selector, e.g. "app=my-app".
        field_selector: Field selector, e.g. "status.phase=Failed".
        unhealthy_only: If True, return only pods not in Running/Succeeded state.

    Returns:
        JSON string with pod names, phases, container statuses, restart counts,
        and pod conditions.
    """
    log = logger.bind(namespace=namespace, label_selector=label_selector)
    log.info("tool.get_cluster_pods.start")

    try:
        core, _ = get_kubernetes_client()
        kwargs: dict[str, Any] = {}
        if label_selector:
            kwargs["label_selector"] = label_selector
        if field_selector:
            kwargs["field_selector"] = field_selector

        if namespace:
            pod_list = core.list_namespaced_pod(namespace, **kwargs)
        else:
            pod_list = core.list_pod_for_all_namespaces(**kwargs)

        pods = []
        for pod in pod_list.items:
            containers = []
            for cs in pod.status.container_statuses or []:
                state = "unknown"
                state_detail: dict[str, Any] = {}
                if cs.state:
                    if cs.state.running:
                        state = "running"
                        state_detail = {"started_at": _dt(cs.state.running.started_at)}
                    elif cs.state.waiting:
                        state = "waiting"
                        state_detail = {
                            "reason": cs.state.waiting.reason,
                            "message": cs.state.waiting.message,
                        }
                    elif cs.state.terminated:
                        state = "terminated"
                        state_detail = {
                            "reason": cs.state.terminated.reason,
                            "exit_code": cs.state.terminated.exit_code,
                            "message": cs.state.terminated.message,
                        }
                containers.append(
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "state": state,
                        "state_detail": state_detail,
                    }
                )

            phase = pod.status.phase or "Unknown"
            pod_entry = {
                **_safe_meta(pod),
                "phase": phase,
                "conditions": _condition_list(pod.status.conditions),
                "containers": containers,
                "host_ip": pod.status.host_ip,
                "pod_ip": pod.status.pod_ip,
                "start_time": _dt(pod.status.start_time),
                "node_name": pod.spec.node_name if pod.spec else None,
            }

            if unhealthy_only and phase in ("Running", "Succeeded"):
                continue
            pods.append(pod_entry)

        log.info("tool.get_cluster_pods.done", count=len(pods))
        return json.dumps({"pods": pods, "count": len(pods)})

    except ApiException as exc:
        log.warning("tool.get_cluster_pods.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_deployments(namespace: str | None = None) -> str:
    """
    List Deployments and their rollout status across the cluster.

    Args:
        namespace: Namespace to query; omit for all namespaces.

    Returns:
        JSON string with deployment names, desired/ready/available/updated
        replica counts, and conditions.
    """
    log = logger.bind(namespace=namespace)
    log.info("tool.get_cluster_deployments.start")

    try:
        _, apps = get_kubernetes_client()
        if namespace:
            dep_list = apps.list_namespaced_deployment(namespace)
        else:
            dep_list = apps.list_deployment_for_all_namespaces()

        deployments = []
        for d in dep_list.items:
            s = d.status
            spec_replicas = d.spec.replicas if d.spec else 1
            deployments.append(
                {
                    **_safe_meta(d),
                    "desired": spec_replicas,
                    "ready": s.ready_replicas or 0,
                    "available": s.available_replicas or 0,
                    "updated": s.updated_replicas or 0,
                    "unavailable": s.unavailable_replicas or 0,
                    "healthy": (s.ready_replicas or 0) == (spec_replicas or 0),
                    "conditions": _condition_list(s.conditions),
                    "strategy": d.spec.strategy.type if d.spec and d.spec.strategy else None,
                }
            )

        unhealthy = [d["name"] for d in deployments if not d["healthy"]]
        log.info("tool.get_cluster_deployments.done", total=len(deployments), unhealthy=unhealthy)
        return json.dumps(
            {"deployments": deployments, "count": len(deployments), "unhealthy": unhealthy}
        )

    except ApiException as exc:
        log.warning("tool.get_cluster_deployments.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_daemonsets(namespace: str | None = None) -> str:
    """
    List DaemonSets and their current scheduling status.

    Args:
        namespace: Namespace to query; omit for all namespaces.

    Returns:
        JSON string with DaemonSet names, desired/scheduled/ready/misscheduled
        counts, and conditions.
    """
    log = logger.bind(namespace=namespace)
    log.info("tool.get_cluster_daemonsets.start")

    try:
        _, apps = get_kubernetes_client()
        if namespace:
            ds_list = apps.list_namespaced_daemon_set(namespace)
        else:
            ds_list = apps.list_daemon_set_for_all_namespaces()

        daemonsets = []
        for ds in ds_list.items:
            s = ds.status
            daemonsets.append(
                {
                    **_safe_meta(ds),
                    "desired": s.desired_number_scheduled,
                    "current": s.current_number_scheduled,
                    "ready": s.number_ready,
                    "available": s.number_available or 0,
                    "unavailable": s.number_unavailable or 0,
                    "misscheduled": s.number_misscheduled,
                    "updated": s.updated_number_scheduled or 0,
                    "healthy": s.number_ready == s.desired_number_scheduled,
                    "conditions": _condition_list(getattr(s, "conditions", None)),
                }
            )

        unhealthy = [d["name"] for d in daemonsets if not d["healthy"]]
        log.info("tool.get_cluster_daemonsets.done", total=len(daemonsets), unhealthy=unhealthy)
        return json.dumps(
            {"daemonsets": daemonsets, "count": len(daemonsets), "unhealthy": unhealthy}
        )

    except ApiException as exc:
        log.warning("tool.get_cluster_daemonsets.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_statefulsets(namespace: str | None = None) -> str:
    """
    List StatefulSets and their rollout status.

    Args:
        namespace: Namespace to query; omit for all namespaces.

    Returns:
        JSON string with StatefulSet names, replica counts, and readiness.
    """
    log = logger.bind(namespace=namespace)
    log.info("tool.get_cluster_statefulsets.start")

    try:
        _, apps = get_kubernetes_client()
        if namespace:
            sts_list = apps.list_namespaced_stateful_set(namespace)
        else:
            sts_list = apps.list_stateful_set_for_all_namespaces()

        statefulsets = []
        for sts in sts_list.items:
            s = sts.status
            spec_replicas = sts.spec.replicas if sts.spec else 1
            statefulsets.append(
                {
                    **_safe_meta(sts),
                    "desired": spec_replicas,
                    "ready": s.ready_replicas or 0,
                    "current": s.current_replicas or 0,
                    "updated": s.updated_replicas or 0,
                    "healthy": (s.ready_replicas or 0) == (spec_replicas or 0),
                    "current_revision": s.current_revision,
                    "update_revision": s.update_revision,
                }
            )

        unhealthy = [s["name"] for s in statefulsets if not s["healthy"]]
        log.info("tool.get_cluster_statefulsets.done", total=len(statefulsets), unhealthy=unhealthy)
        return json.dumps(
            {"statefulsets": statefulsets, "count": len(statefulsets), "unhealthy": unhealthy}
        )

    except ApiException as exc:
        log.warning("tool.get_cluster_statefulsets.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_events(
    namespace: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> str:
    """
    List recent cluster events, optionally filtered by type.

    Args:
        namespace:  Namespace to query; omit for all namespaces.
        event_type: Filter by type: "Warning", "Normal". Omit for all.
        limit:      Maximum events to return (default 100, max 500).

    Returns:
        JSON string with event reason, message, involved object, count,
        first/last seen timestamps, and type.
    """
    effective_limit = min(limit, 500)
    log = logger.bind(namespace=namespace, event_type=event_type, limit=effective_limit)
    log.info("tool.get_cluster_events.start")

    try:
        core, _ = get_kubernetes_client()
        kwargs: dict[str, Any] = {"limit": effective_limit}
        if event_type:
            kwargs["field_selector"] = f"type={event_type}"

        if namespace:
            event_list = core.list_namespaced_event(namespace, **kwargs)
        else:
            event_list = core.list_event_for_all_namespaces(**kwargs)

        events = []
        for ev in event_list.items:
            events.append(
                {
                    "namespace": ev.metadata.namespace,
                    "name": ev.metadata.name,
                    "type": ev.type,
                    "reason": ev.reason,
                    "message": ev.message,
                    "count": ev.count or 1,
                    "first_time": _dt(ev.first_timestamp),
                    "last_time": _dt(ev.last_timestamp),
                    "involved_object": {
                        "kind": ev.involved_object.kind,
                        "namespace": ev.involved_object.namespace,
                        "name": ev.involved_object.name,
                    }
                    if ev.involved_object
                    else None,
                    "source": {
                        "component": ev.source.component if ev.source else None,
                        "host": ev.source.host if ev.source else None,
                    },
                }
            )

        # Sort by last_time desc (most recent first)
        events.sort(key=lambda e: e["last_time"] or "", reverse=True)
        log.info("tool.get_cluster_events.done", count=len(events))
        return json.dumps({"events": events, "count": len(events)})

    except ApiException as exc:
        log.warning("tool.get_cluster_events.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_namespaces() -> str:
    """
    List all namespaces in the cluster with their phase and labels.

    Returns:
        JSON string with namespace names, phases, and labels.
    """
    logger.info("tool.get_cluster_namespaces.start")
    try:
        core, _ = get_kubernetes_client()
        ns_list = core.list_namespace()
        namespaces = [
            {
                **_safe_meta(ns),
                "phase": ns.status.phase if ns.status else None,
            }
            for ns in ns_list.items
        ]
        logger.info("tool.get_cluster_namespaces.done", count=len(namespaces))
        return json.dumps({"namespaces": namespaces, "count": len(namespaces)})

    except ApiException as exc:
        logger.warning("tool.get_cluster_namespaces.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_pvc_status(namespace: str | None = None) -> str:
    """
    List PersistentVolumeClaims and their binding status.

    Args:
        namespace: Namespace to query; omit for all namespaces.

    Returns:
        JSON string with PVC names, phases, storage classes, access modes,
        capacity, and bound volume names.
    """
    log = logger.bind(namespace=namespace)
    log.info("tool.get_cluster_pvc_status.start")

    try:
        core, _ = get_kubernetes_client()
        if namespace:
            pvc_list = core.list_namespaced_persistent_volume_claim(namespace)
        else:
            pvc_list = core.list_persistent_volume_claim_for_all_namespaces()

        pvcs = []
        for pvc in pvc_list.items:
            pvcs.append(
                {
                    **_safe_meta(pvc),
                    "phase": pvc.status.phase if pvc.status else None,
                    "storage_class": pvc.spec.storage_class_name if pvc.spec else None,
                    "access_modes": pvc.spec.access_modes if pvc.spec else [],
                    "capacity": pvc.status.capacity if pvc.status else {},
                    "volume_name": pvc.spec.volume_name if pvc.spec else None,
                }
            )

        unbound = [p["name"] for p in pvcs if p["phase"] != "Bound"]
        log.info("tool.get_cluster_pvc_status.done", total=len(pvcs), unbound=unbound)
        return json.dumps({"pvcs": pvcs, "count": len(pvcs), "unbound": unbound})

    except ApiException as exc:
        log.warning("tool.get_cluster_pvc_status.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_services(namespace: str | None = None) -> str:
    """
    List Services and their endpoints in the cluster.

    Args:
        namespace: Namespace to query; omit for all namespaces.

    Returns:
        JSON string with service names, types, cluster IPs, ports,
        external IPs or load-balancer ingresses, and selectors.
    """
    log = logger.bind(namespace=namespace)
    log.info("tool.get_cluster_services.start")

    try:
        core, _ = get_kubernetes_client()
        if namespace:
            svc_list = core.list_namespaced_service(namespace)
        else:
            svc_list = core.list_service_for_all_namespaces()

        services = []
        for svc in svc_list.items:
            lb_ingress = []
            if svc.status and svc.status.load_balancer and svc.status.load_balancer.ingress:
                lb_ingress = [
                    {"ip": ing.ip, "hostname": ing.hostname}
                    for ing in svc.status.load_balancer.ingress
                ]

            services.append(
                {
                    **_safe_meta(svc),
                    "type": svc.spec.type if svc.spec else None,
                    "cluster_ip": svc.spec.cluster_ip if svc.spec else None,
                    "external_ips": svc.spec.external_i_ps or [],
                    "load_balancer_ingress": lb_ingress,
                    "ports": [
                        {
                            "name": p.name,
                            "port": p.port,
                            "target_port": str(p.target_port),
                            "protocol": p.protocol,
                            "node_port": p.node_port,
                        }
                        for p in (svc.spec.ports or [])
                    ]
                    if svc.spec
                    else [],
                    "selector": svc.spec.selector or {} if svc.spec else {},
                }
            )

        log.info("tool.get_cluster_services.done", count=len(services))
        return json.dumps({"services": services, "count": len(services)})

    except ApiException as exc:
        log.warning("tool.get_cluster_services.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})


def get_cluster_component_status() -> str:
    """
    Query the health of Kubernetes control-plane components
    (scheduler, controller-manager, etcd).

    Returns:
        JSON string with component names and their condition status.
    """
    logger.info("tool.get_cluster_component_status.start")
    try:
        core, _ = get_kubernetes_client()
        cs_list = core.list_component_status()
        components = []
        for cs in cs_list.items:
            components.append(
                {
                    "name": cs.metadata.name,
                    "conditions": _condition_list(cs.conditions),
                    "healthy": any(
                        c.type == "Healthy" and c.status == "True"
                        for c in (cs.conditions or [])
                    ),
                }
            )
        unhealthy = [c["name"] for c in components if not c["healthy"]]
        logger.info("tool.get_cluster_component_status.done", total=len(components), unhealthy=unhealthy)
        return json.dumps({"components": components, "count": len(components), "unhealthy": unhealthy})

    except ApiException as exc:
        logger.warning("tool.get_cluster_component_status.error", status=exc.status, reason=exc.reason)
        return json.dumps({"error": f"Kubernetes API error {exc.status}: {exc.reason}"})
