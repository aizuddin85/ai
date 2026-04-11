"""
Unit tests for in-cluster Kubernetes health tools.
The Kubernetes client is mocked so no real cluster is required.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from server.tools.cluster_k8s import (
    get_cluster_daemonsets,
    get_cluster_deployments,
    get_cluster_events,
    get_cluster_namespaces,
    get_cluster_nodes,
    get_cluster_pods,
    get_cluster_pvc_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _meta(name: str, namespace: str = "default") -> MagicMock:
    m = MagicMock()
    m.name = name
    m.namespace = namespace
    m.labels = {"app": name}
    m.annotations = {}
    m.creation_timestamp = _NOW
    m.resource_version = "12345"
    m.uid = "aaaa-bbbb-cccc"
    return m


def _node(name: str, ready: str = "True") -> MagicMock:
    node = MagicMock()
    node.metadata = _meta(name, namespace=None)
    node.metadata.labels = {"node-role.kubernetes.io/agent": ""}
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = ready
    cond.reason = None
    cond.message = None
    cond.last_transition_time = _NOW
    node.status.conditions = [cond]
    node.status.capacity = {"cpu": "4", "memory": "8Gi", "pods": "110"}
    node.status.allocatable = {"cpu": "3900m", "memory": "7Gi", "pods": "110"}
    node.status.node_info.kubelet_version = "v1.29.3"
    node.status.node_info.os_image = "Ubuntu 22.04"
    node.status.node_info.container_runtime_version = "containerd://1.7.0"
    node.status.node_info.kernel_version = "5.15.0"
    node.spec.unschedulable = False
    return node


def _pod(
    name: str,
    namespace: str = "default",
    phase: str = "Running",
    node: str = "node-1",
) -> MagicMock:
    pod = MagicMock()
    pod.metadata = _meta(name, namespace)
    pod.status.phase = phase
    pod.status.conditions = []
    pod.status.host_ip = "10.0.0.1"
    pod.status.pod_ip = "10.1.0.1"
    pod.status.start_time = _NOW

    cs = MagicMock()
    cs.name = "app"
    cs.ready = phase == "Running"
    cs.restart_count = 0
    cs.state.running.started_at = _NOW
    cs.state.waiting = None
    cs.state.terminated = None
    pod.status.container_statuses = [cs]
    pod.spec.node_name = node
    return pod


def _deployment(
    name: str,
    desired: int = 3,
    ready: int = 3,
    available: int = 3,
) -> MagicMock:
    d = MagicMock()
    d.metadata = _meta(name)
    d.spec.replicas = desired
    d.spec.strategy.type = "RollingUpdate"
    d.status.ready_replicas = ready
    d.status.available_replicas = available
    d.status.updated_replicas = desired
    d.status.unavailable_replicas = desired - available
    d.status.conditions = []
    return d


def _daemonset(
    name: str,
    desired: int = 3,
    ready: int = 3,
) -> MagicMock:
    ds = MagicMock()
    ds.metadata = _meta(name, namespace="kube-system")
    ds.status.desired_number_scheduled = desired
    ds.status.current_number_scheduled = desired
    ds.status.number_ready = ready
    ds.status.number_available = ready
    ds.status.number_unavailable = desired - ready
    ds.status.number_misscheduled = 0
    ds.status.updated_number_scheduled = desired
    return ds


# ---------------------------------------------------------------------------
# get_cluster_nodes
# ---------------------------------------------------------------------------


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_nodes_all_ready(mock_k8s: MagicMock) -> None:
    core = MagicMock()
    core.list_node.return_value.items = [_node("node-1"), _node("node-2")]
    mock_k8s.return_value = (core, MagicMock())

    result = json.loads(get_cluster_nodes())
    assert result["count"] == 2
    assert result["not_ready"] == []
    assert result["nodes"][0]["ready"] == "True"


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_nodes_with_not_ready(mock_k8s: MagicMock) -> None:
    core = MagicMock()
    core.list_node.return_value.items = [
        _node("node-1"),
        _node("node-2", ready="False"),
    ]
    mock_k8s.return_value = (core, MagicMock())

    result = json.loads(get_cluster_nodes())
    assert result["count"] == 2
    assert "node-2" in result["not_ready"]


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_nodes_label_selector(mock_k8s: MagicMock) -> None:
    core = MagicMock()
    core.list_node.return_value.items = [_node("node-1")]
    mock_k8s.return_value = (core, MagicMock())

    get_cluster_nodes(label_selector="agentpool=system")
    core.list_node.assert_called_once_with(label_selector="agentpool=system")


# ---------------------------------------------------------------------------
# get_cluster_pods
# ---------------------------------------------------------------------------


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_pods_all_namespaces(mock_k8s: MagicMock) -> None:
    core = MagicMock()
    core.list_pod_for_all_namespaces.return_value.items = [
        _pod("pod-a"),
        _pod("pod-b"),
    ]
    mock_k8s.return_value = (core, MagicMock())

    result = json.loads(get_cluster_pods())
    assert result["count"] == 2


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_pods_unhealthy_only(mock_k8s: MagicMock) -> None:
    core = MagicMock()
    core.list_pod_for_all_namespaces.return_value.items = [
        _pod("healthy-pod", phase="Running"),
        _pod("failed-pod", phase="Failed"),
        _pod("pending-pod", phase="Pending"),
    ]
    mock_k8s.return_value = (core, MagicMock())

    result = json.loads(get_cluster_pods(unhealthy_only=True))
    assert result["count"] == 2
    names = [p["name"] for p in result["pods"]]
    assert "healthy-pod" not in names
    assert "failed-pod" in names


# ---------------------------------------------------------------------------
# get_cluster_deployments
# ---------------------------------------------------------------------------


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_deployments_healthy(mock_k8s: MagicMock) -> None:
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value.items = [
        _deployment("web", desired=3, ready=3),
    ]
    mock_k8s.return_value = (MagicMock(), apps)

    result = json.loads(get_cluster_deployments())
    assert result["unhealthy"] == []
    assert result["deployments"][0]["healthy"] is True


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_deployments_unhealthy(mock_k8s: MagicMock) -> None:
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value.items = [
        _deployment("api", desired=3, ready=1, available=1),
    ]
    mock_k8s.return_value = (MagicMock(), apps)

    result = json.loads(get_cluster_deployments())
    assert "api" in result["unhealthy"]
    assert result["deployments"][0]["healthy"] is False


# ---------------------------------------------------------------------------
# get_cluster_daemonsets
# ---------------------------------------------------------------------------


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_daemonsets(mock_k8s: MagicMock) -> None:
    apps = MagicMock()
    apps.list_daemon_set_for_all_namespaces.return_value.items = [
        _daemonset("kube-proxy", desired=3, ready=3),
        _daemonset("azure-cni", desired=3, ready=2),
    ]
    mock_k8s.return_value = (MagicMock(), apps)

    result = json.loads(get_cluster_daemonsets())
    assert result["count"] == 2
    assert "azure-cni" in result["unhealthy"]
    assert "kube-proxy" not in result["unhealthy"]


# ---------------------------------------------------------------------------
# get_cluster_events
# ---------------------------------------------------------------------------


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_events_warning_filter(mock_k8s: MagicMock) -> None:
    core = MagicMock()

    ev = MagicMock()
    ev.metadata.namespace = "default"
    ev.metadata.name = "pod-crash.abc"
    ev.type = "Warning"
    ev.reason = "BackOff"
    ev.message = "Back-off restarting failed container"
    ev.count = 5
    ev.first_timestamp = _NOW
    ev.last_timestamp = _NOW
    ev.involved_object.kind = "Pod"
    ev.involved_object.namespace = "default"
    ev.involved_object.name = "my-pod"
    ev.source.component = "kubelet"
    ev.source.host = "node-1"

    core.list_event_for_all_namespaces.return_value.items = [ev]
    mock_k8s.return_value = (core, MagicMock())

    result = json.loads(get_cluster_events(event_type="Warning"))
    assert result["count"] == 1
    assert result["events"][0]["reason"] == "BackOff"
    core.list_event_for_all_namespaces.assert_called_once_with(
        limit=100, field_selector="type=Warning"
    )


# ---------------------------------------------------------------------------
# get_cluster_namespaces
# ---------------------------------------------------------------------------


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_namespaces(mock_k8s: MagicMock) -> None:
    core = MagicMock()
    ns1, ns2 = MagicMock(), MagicMock()
    ns1.metadata = _meta("default", namespace=None)
    ns2.metadata = _meta("kube-system", namespace=None)
    ns1.status.phase = "Active"
    ns2.status.phase = "Active"
    core.list_namespace.return_value.items = [ns1, ns2]
    mock_k8s.return_value = (core, MagicMock())

    result = json.loads(get_cluster_namespaces())
    assert result["count"] == 2
    names = [n["name"] for n in result["namespaces"]]
    assert "default" in names
    assert "kube-system" in names


# ---------------------------------------------------------------------------
# get_cluster_pvc_status
# ---------------------------------------------------------------------------


@patch("server.tools.cluster_k8s.get_kubernetes_client")
def test_get_cluster_pvc_status_with_unbound(mock_k8s: MagicMock) -> None:
    core = MagicMock()

    bound_pvc = MagicMock()
    bound_pvc.metadata = _meta("data-pvc")
    bound_pvc.status.phase = "Bound"
    bound_pvc.spec.storage_class_name = "managed-premium"
    bound_pvc.spec.access_modes = ["ReadWriteOnce"]
    bound_pvc.status.capacity = {"storage": "10Gi"}
    bound_pvc.spec.volume_name = "pvc-aaaa"

    pending_pvc = MagicMock()
    pending_pvc.metadata = _meta("logs-pvc")
    pending_pvc.status.phase = "Pending"
    pending_pvc.spec.storage_class_name = "standard"
    pending_pvc.spec.access_modes = ["ReadWriteMany"]
    pending_pvc.status.capacity = {}
    pending_pvc.spec.volume_name = None

    core.list_persistent_volume_claim_for_all_namespaces.return_value.items = [
        bound_pvc,
        pending_pvc,
    ]
    mock_k8s.return_value = (core, MagicMock())

    result = json.loads(get_cluster_pvc_status())
    assert result["count"] == 2
    assert "logs-pvc" in result["unbound"]
    assert "data-pvc" not in result["unbound"]
