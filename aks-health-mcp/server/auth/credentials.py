"""
Azure and Kubernetes credential factories.

Azure credential chain (highest-priority first):
  1. Service Principal  – when AZURE_CLIENT_ID + AZURE_CLIENT_SECRET are set
                          (robotic/CI use-case)
  2. Azure CLI          – when the user has run `az login`
                          (interactive/developer use-case)
  3. Managed Identity   – when running inside an Azure-hosted workload
                          (in-cluster pod with workload identity)

Kubernetes credential:
  1. In-cluster ServiceAccount token
  2. Kubeconfig file (with optional context override)

All credential objects are cached as singletons for the process lifetime
so we don't re-authenticate on every tool call.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

import structlog
from azure.identity import (
    AzureCliCredential,
    ChainedTokenCredential,
    ClientSecretCredential,
    ManagedIdentityCredential,
)

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_azure_credential() -> TokenCredential:
    """
    Build the Azure credential chain.

    The function is intentionally free of Settings import to avoid a
    circular dependency; it reads env vars directly so it can be called
    before Settings initialisation if needed.
    """
    client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "").strip()
    tenant_id = os.getenv("AZURE_TENANT_ID", "").strip()

    credentials: list[TokenCredential] = []

    if client_id and client_secret and tenant_id:
        logger.info(
            "azure.auth.mode",
            mode="service_principal",
            client_id=client_id,
            tenant_id=tenant_id,
        )
        credentials.append(
            ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
        )
    else:
        logger.info("azure.auth.mode", mode="user_or_managed_identity")

    # Always add CLI and MI as fallbacks
    credentials.extend(
        [
            AzureCliCredential(),
            ManagedIdentityCredential(),
        ]
    )

    return ChainedTokenCredential(*credentials)


def get_kubernetes_client() -> tuple[object, object]:
    """
    Return (CoreV1Api, AppsV1Api) clients, loading config from the
    appropriate source.

    Returns a tuple so callers don't need to import kubernetes directly.
    """
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    in_cluster_forced = os.getenv("K8S_IN_CLUSTER", "false").lower() == "true"
    in_cluster_auto = bool(os.getenv("KUBERNETES_SERVICE_HOST"))

    if in_cluster_forced or in_cluster_auto:
        logger.info("k8s.auth.mode", mode="in_cluster")
        k8s_config.load_incluster_config()
    else:
        kubeconfig = os.getenv("KUBECONFIG") or None
        context = os.getenv("K8S_CONTEXT") or None
        logger.info("k8s.auth.mode", mode="kubeconfig", context=context or "default")
        k8s_config.load_kube_config(config_file=kubeconfig, context=context)

    configuration = k8s_client.Configuration.get_default_copy()
    api_client = k8s_client.ApiClient(configuration)
    return (
        k8s_client.CoreV1Api(api_client),
        k8s_client.AppsV1Api(api_client),
    )
