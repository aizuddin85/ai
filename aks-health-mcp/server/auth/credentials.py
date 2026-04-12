"""
Azure and Kubernetes credential factories.

Azure credential chain (highest-priority first):
  1. OBO ARM token  – when AZURE_ARM_TOKEN is set (per-request, user's identity)
                      This token is injected by the API layer after an OBO exchange
                      so all Azure SDK calls run under the signed-in user's identity,
                      inheriting their Azure RBAC role assignments.
  2. Azure CLI      – when the user has run `az login`
                      (interactive/developer use-case; no AZURE_ARM_TOKEN)
  3. Managed Identity – when running inside an Azure-hosted workload
                        (in-cluster pod with Workload Identity)

Note: the AZURE_ARM_TOKEN path is not cached with lru_cache because the
token changes on every request.  The fallback chain (CLI / MI) IS cached.

Kubernetes credential:
  1. In-cluster ServiceAccount token
  2. Kubeconfig file (with optional context override)

Kubernetes always uses the server's own service account — it is not
subject to the OBO flow.
"""
from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import structlog
from azure.core.credentials import AccessToken
from azure.identity import (
    AzureCliCredential,
    ChainedTokenCredential,
    ManagedIdentityCredential,
)

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Static token credential (wraps a pre-obtained access token)
# ---------------------------------------------------------------------------


class StaticTokenCredential:
    """
    Wraps a pre-obtained Azure access token as a TokenCredential.

    Used to inject the OBO-exchanged ARM token into the MCP server so all
    Azure SDK calls run under the signed-in user's identity.

    The token expiry is set conservatively to 1 hour from construction; the
    actual expiry is embedded in the JWT and enforced by Azure.
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._expires_on = int(time.time()) + 3600

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:  # noqa: ARG002
        return AccessToken(self._token, self._expires_on)


# ---------------------------------------------------------------------------
# Azure credential factory
# ---------------------------------------------------------------------------


def get_azure_credential() -> "TokenCredential":
    """
    Return the appropriate Azure credential for this process invocation.

    When AZURE_ARM_TOKEN is set (injected by the API layer from an OBO
    exchange), a StaticTokenCredential is returned immediately — no caching,
    because the token is user-scoped and changes per request.

    Otherwise the cached fallback chain (AzureCliCredential →
    ManagedIdentityCredential) is returned.
    """
    arm_token = os.getenv("AZURE_ARM_TOKEN", "").strip()
    if arm_token:
        logger.info("azure.auth.mode", mode="obo_user_token")
        return StaticTokenCredential(arm_token)

    return _get_cached_credential()


@lru_cache(maxsize=1)
def _get_cached_credential() -> "TokenCredential":
    """
    Build and cache the server-level Azure credential chain.

    Used when no per-request OBO token is available:
      - Local development: AzureCliCredential (after `az login`)
      - Production / AKS pod: ManagedIdentityCredential (Workload Identity)
    """
    logger.info("azure.auth.mode", mode="cli_or_managed_identity")
    return ChainedTokenCredential(
        AzureCliCredential(),
        ManagedIdentityCredential(),
    )


def get_kubernetes_client() -> tuple[object, object]:
    """
    Return (CoreV1Api, AppsV1Api) clients authenticated for the caller.

    Authentication priority
    -----------------------
    1. AZURE_K8S_TOKEN is set (per-request OBO token injected by the API layer)
       → Azure RBAC mode: cluster server + CA are read from kubeconfig or
         in-cluster config, then the bearer token is replaced with the user's
         OBO token scoped to the AKS server application.  Azure RBAC role
         assignments on the user's identity control what Kubernetes resources
         are returned.  No local ClusterRole/ServiceAccount needed.
    2. AZURE_K8S_TOKEN absent
       → Local developer mode: the full kubeconfig (including its auth
         stanza) is used unchanged.  Run `kubelogin convert-kubeconfig`
         or `az aks get-credentials` before using this path.

    Returns a tuple so callers don't need to import kubernetes directly.
    """
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    k8s_token = os.getenv("AZURE_K8S_TOKEN", "").strip()
    in_cluster = (
        os.getenv("K8S_IN_CLUSTER", "false").lower() == "true"
        or bool(os.getenv("KUBERNETES_SERVICE_HOST"))
    )

    # Load server URL + CA cert from kubeconfig or in-cluster projection.
    # The auth stanza is overridden below when running in Azure RBAC mode.
    if in_cluster:
        k8s_config.load_incluster_config()
    else:
        kubeconfig = os.getenv("KUBECONFIG") or None
        context = os.getenv("K8S_CONTEXT") or None
        k8s_config.load_kube_config(config_file=kubeconfig, context=context)

    configuration = k8s_client.Configuration.get_default_copy()

    if k8s_token:
        # Override service-account / kubeconfig auth with the user's OBO token.
        # AKS (Azure RBAC enabled) validates the token against Azure AD and
        # enforces role assignments without any local RBAC objects.
        configuration.api_key = {"authorization": f"Bearer {k8s_token}"}
        configuration.api_key_prefix = {}
        logger.info("k8s.auth.mode", mode="azure_rbac_obo")
    else:
        logger.info(
            "k8s.auth.mode",
            mode="kubeconfig",
            in_cluster=in_cluster,
        )

    api_client = k8s_client.ApiClient(configuration)
    return (
        k8s_client.CoreV1Api(api_client),
        k8s_client.AppsV1Api(api_client),
    )
