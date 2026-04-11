"""
Unit tests for Azure AKS tools.
Azure SDK clients are mocked so no real Azure connection is needed.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from server.tools.azure_aks import (
    get_aks_node_pools,
    get_aks_upgrade_profile,
    get_resource_health_events,
    list_aks_clusters,
)


# ---------------------------------------------------------------------------
# Helpers to build mock SDK objects
# ---------------------------------------------------------------------------


def _mock_cluster(
    name: str = "my-aks",
    rg: str = "my-rg",
    sub: str = "11111111-1111-1111-1111-111111111111",
    location: str = "eastus",
    k8s_version: str = "1.29.3",
    provisioning_state: str = "Succeeded",
    power_code: str = "Running",
) -> MagicMock:
    c = MagicMock()
    c.name = name
    c.id = f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.ContainerService/managedClusters/{name}"
    c.location = location
    c.kubernetes_version = k8s_version
    c.provisioning_state = provisioning_state
    c.power_state.code = power_code
    c.fqdn = f"{name}.hcp.{location}.azmk8s.io"
    c.node_resource_group = f"MC_{rg}_{name}_{location}"
    c.tags = {"env": "prod"}
    return c


def _mock_node_pool(
    name: str = "nodepool1",
    vm_size: str = "Standard_D4s_v3",
    count: int = 3,
) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.vm_size = vm_size
    p.count = count
    p.min_count = 1
    p.max_count = 10
    p.enable_auto_scaling = True
    p.os_type = "Linux"
    p.os_disk_size_gb = 128
    p.kubernetes_version = "1.29.3"
    p.provisioning_state = "Succeeded"
    p.power_state.code = "Running"
    p.node_labels = {"purpose": "general"}
    p.node_taints = []
    p.mode = "System"
    p.upgrade_settings = None
    return p


# ---------------------------------------------------------------------------
# list_aks_clusters
# ---------------------------------------------------------------------------


@patch("server.tools.azure_aks.ContainerServiceClient")
@patch("server.tools.azure_aks.get_azure_credential")
def test_list_aks_clusters_success(mock_cred: MagicMock, mock_cs: MagicMock) -> None:
    mock_cred.return_value = MagicMock()
    mock_instance = mock_cs.return_value
    mock_instance.managed_clusters.list.return_value = [
        _mock_cluster("cluster-1"),
        _mock_cluster("cluster-2", provisioning_state="Updating"),
    ]

    result = json.loads(
        list_aks_clusters(subscription_id="11111111-1111-1111-1111-111111111111")
    )
    assert result["count"] == 2
    names = [c["name"] for c in result["clusters"]]
    assert "cluster-1" in names
    assert "cluster-2" in names


@patch("server.tools.azure_aks.ContainerServiceClient")
@patch("server.tools.azure_aks.get_azure_credential")
def test_list_aks_clusters_with_resource_group(
    mock_cred: MagicMock, mock_cs: MagicMock
) -> None:
    mock_cred.return_value = MagicMock()
    mock_instance = mock_cs.return_value
    mock_instance.managed_clusters.list_by_resource_group.return_value = [
        _mock_cluster("scoped-cluster")
    ]

    result = json.loads(
        list_aks_clusters(
            subscription_id="11111111-1111-1111-1111-111111111111",
            resource_group="my-rg",
        )
    )
    assert result["count"] == 1
    mock_instance.managed_clusters.list_by_resource_group.assert_called_once_with("my-rg")


def test_list_aks_clusters_no_subscription() -> None:
    result = json.loads(list_aks_clusters(subscription_id=""))
    assert "error" in result


# ---------------------------------------------------------------------------
# get_aks_node_pools
# ---------------------------------------------------------------------------


@patch("server.tools.azure_aks.ContainerServiceClient")
@patch("server.tools.azure_aks.get_azure_credential")
def test_get_aks_node_pools_success(mock_cred: MagicMock, mock_cs: MagicMock) -> None:
    mock_cred.return_value = MagicMock()
    mock_instance = mock_cs.return_value
    mock_instance.agent_pools.list.return_value = [
        _mock_node_pool("system", count=3),
        _mock_node_pool("user", count=5),
    ]

    result = json.loads(
        get_aks_node_pools(
            cluster_name="my-aks",
            resource_group="my-rg",
            subscription_id="11111111-1111-1111-1111-111111111111",
        )
    )
    assert result["count"] == 2
    assert result["node_pools"][0]["name"] == "system"
    assert result["node_pools"][1]["count"] == 5


# ---------------------------------------------------------------------------
# get_aks_upgrade_profile
# ---------------------------------------------------------------------------


@patch("server.tools.azure_aks.ContainerServiceClient")
@patch("server.tools.azure_aks.get_azure_credential")
def test_get_aks_upgrade_profile_success(mock_cred: MagicMock, mock_cs: MagicMock) -> None:
    mock_cred.return_value = MagicMock()
    mock_instance = mock_cs.return_value

    cp = MagicMock()
    cp.kubernetes_version = "1.29.3"
    cp.upgrades = [
        MagicMock(kubernetes_version="1.30.1", is_preview=False),
        MagicMock(kubernetes_version="1.30.2", is_preview=True),
    ]

    ap = MagicMock()
    ap.name = "nodepool1"
    ap.kubernetes_version = "1.29.3"
    ap.upgrades = [MagicMock(kubernetes_version="1.30.1", is_preview=False)]

    profile = MagicMock()
    profile.control_plane_profile = cp
    profile.agent_pool_profiles = [ap]
    mock_instance.managed_clusters.get_upgrade_profile.return_value = profile

    result = json.loads(
        get_aks_upgrade_profile(
            cluster_name="my-aks",
            resource_group="my-rg",
            subscription_id="11111111-1111-1111-1111-111111111111",
        )
    )
    assert result["control_plane"]["kubernetes_version"] == "1.29.3"
    assert len(result["control_plane"]["upgrades"]) == 2
    assert result["agent_pools"][0]["name"] == "nodepool1"
