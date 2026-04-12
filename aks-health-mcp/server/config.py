"""
Configuration management via pydantic-settings.
Supports .env file and environment variables.
Sensitive fields (secrets) are masked in __repr__ and logs.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Prevent extra env vars from leaking into config
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Azure authentication
    # ------------------------------------------------------------------
    azure_tenant_id: str = Field(..., description="Azure AD tenant ID")

    # Stored as a raw comma-separated string so pydantic-settings reads it
    # as a plain string (not JSON).  Use the `subscription_ids` property to
    # get the parsed list.
    # Example: "sub1-guid,sub2-guid,sub3-guid"
    azure_subscription_ids: str | None = Field(
        default=None,
        description=(
            "Comma-separated Azure subscription IDs to query, e.g. "
            "'11111111-...,22222222-...'.  A single ID is also accepted."
        ),
    )

    # Backend app credentials – used for the On-Behalf-Of (OBO) token exchange.
    # Set AZURE_CLIENT_SECRET to the client secret of the same app registration
    # as AZURE_AD_APP_CLIENT_ID.  When set, Azure API calls run as the signed-in
    # user (inheriting their Azure RBAC).  When blank, falls back to
    # AzureCliCredential (az login) or ManagedIdentityCredential (Workload Identity).
    azure_client_secret: SecretStr | None = Field(
        default=None,
        description=(
            "Client secret of the Azure AD app registration (for OBO exchange). "
            "Masked in logs."
        ),
    )

    # Per-request ARM token injected by the agent layer after OBO exchange.
    # Set automatically via AZURE_ARM_TOKEN env var in the MCP subprocess;
    # do not set this manually.
    azure_arm_token: str | None = Field(
        default=None,
        description="OBO-exchanged ARM access token (injected per-request into subprocess).",
    )

    # Per-request Kubernetes API token injected by the agent layer after OBO
    # exchange against the AKS server application (6dae42f8-…).
    # Set automatically via AZURE_K8S_TOKEN env var in the MCP subprocess;
    # do not set this manually.
    azure_k8s_token: str | None = Field(
        default=None,
        description=(
            "OBO-exchanged AKS Kubernetes API token (injected per-request into subprocess). "
            "Used when AKS Azure RBAC integration is enabled — replaces ServiceAccount auth."
        ),
    )

    # ------------------------------------------------------------------
    # Kubernetes authentication
    # ------------------------------------------------------------------
    kubeconfig: str | None = Field(default=None, description="Path to kubeconfig file")
    k8s_context: str | None = Field(default=None, description="Kubeconfig context to use")
    k8s_in_cluster: bool = Field(default=False, description="Force in-cluster auth mode")

    # ------------------------------------------------------------------
    # MCP server
    # ------------------------------------------------------------------
    mcp_transport: Literal["stdio", "sse"] = Field(
        default="stdio", description="MCP transport layer"
    )
    mcp_host: str = Field(default="127.0.0.1", description="SSE host (sse transport only)")
    mcp_port: int = Field(default=8090, description="SSE port (sse transport only)")
    api_timeout_seconds: int = Field(default=30, description="Azure/K8s API call timeout")

    # ------------------------------------------------------------------
    # Azure AI Foundry (agents)
    # The endpoint is the Azure AI Foundry project inference URL, e.g.:
    #   https://<project>.services.ai.azure.com/models
    # Auth reuses the same Azure credential chain (SP or user).
    # Set foundry_api_key ONLY when using key-based auth instead of Azure AD.
    # ------------------------------------------------------------------
    azure_foundry_endpoint: str = Field(
        ..., description="Azure AI Foundry inference endpoint URL"
    )
    azure_foundry_model: str = Field(
        ...,
        description="Model deployment name in Azure AI Foundry",
    )
    azure_foundry_api_key: SecretStr | None = Field(
        default=None,
        description="Foundry API key (masked in logs). Leave blank to use Azure AD credential.",
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("azure_tenant_id")
    @classmethod
    def tenant_must_be_guid(cls, v: str) -> str:
        import re

        if not re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            v.lower(),
        ):
            raise ValueError("azure_tenant_id must be a valid GUID")
        return v

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def subscription_ids(self) -> list[str]:
        """Return the configured subscription IDs as a list.

        Parses the comma-separated AZURE_SUBSCRIPTION_IDS value.
        Returns an empty list when the env var is not set.
        """
        if not self.azure_subscription_ids:
            return []
        return [s.strip() for s in self.azure_subscription_ids.split(",") if s.strip()]

    @property
    def azure_subscription_id(self) -> str | None:
        """Return the first configured subscription ID.

        Kept for backward compatibility. Prefer subscription_ids when
        iterating across multiple subscriptions.
        """
        ids = self.subscription_ids
        return ids[0] if ids else None

    @property
    def uses_service_principal(self) -> bool:
        """True when a client secret is configured (used for OBO or direct auth)."""
        return bool(self.azure_client_secret)

    @property
    def uses_foundry_key_auth(self) -> bool:
        """True when key-based auth is configured for Azure AI Foundry."""
        return self.azure_foundry_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()  # type: ignore[call-arg]
