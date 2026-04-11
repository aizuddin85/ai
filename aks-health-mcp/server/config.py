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
    azure_subscription_id: str | None = Field(
        default=None, description="Default Azure subscription ID"
    )
    # SP credentials – optional; absence triggers user/CLI auth
    azure_client_id: str | None = Field(default=None, description="Service principal app ID")
    azure_client_secret: SecretStr | None = Field(
        default=None, description="Service principal secret (masked in logs)"
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
    # Anthropic (agents)
    # ------------------------------------------------------------------
    anthropic_api_key: SecretStr = Field(..., description="Anthropic API key (masked in logs)")
    claude_model: str = Field(default="claude-sonnet-4-6", description="Claude model for agents")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")

    # ------------------------------------------------------------------
    # Derived helpers
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

    @property
    def uses_service_principal(self) -> bool:
        """True when SP credentials are fully configured."""
        return bool(self.azure_client_id and self.azure_client_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()  # type: ignore[call-arg]
