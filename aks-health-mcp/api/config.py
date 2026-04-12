"""
API-layer configuration (extends the MCP server settings).

New settings required for the FastAPI backend:

  AZURE_AD_APP_CLIENT_ID   – Client ID of the Azure AD app registration
                             used for frontend SSO.  Validated as the
                             'aud' claim in every bearer token.
  AZURE_CLIENT_SECRET      – Client secret of the same app registration.
                             Used to perform the On-Behalf-Of (OBO) token
                             exchange so Azure API calls run as the signed-in
                             user.  Leave blank to fall back to AzureCliCredential
                             or Managed Identity (Workload Identity).
  FRONTEND_ORIGIN          – CORS-allowed origin, e.g. https://aks-health.contoso.com
  API_HOST / API_PORT      – Uvicorn bind address (default 0.0.0.0:8000).

Authorization model
-------------------
Any authenticated Azure AD user may call the API.  Azure RBAC on the
user's identity determines which subscriptions and resources are returned:
  - User has Reader on a subscription → clusters are listed and queried.
  - User lacks access → Azure returns 403, which is surfaced as an error
    in the tool result.

No AZURE_AD_ALLOWED_GROUP is needed.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from server.config import Settings


class ApiSettings(Settings):
    """All MCP-server settings plus additional API / auth fields."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----------------------------------------------------------------
    # Azure AD – frontend SSO
    # ----------------------------------------------------------------
    azure_ad_app_client_id: str = Field(
        ...,
        description=(
            "Client ID of the Azure AD app registration used for the frontend SSO. "
            "This is validated as the 'aud' claim in every bearer token."
        ),
    )

    # ----------------------------------------------------------------
    # CORS / server
    # ----------------------------------------------------------------
    frontend_origin: str = Field(
        ...,
        description="Allowed CORS origin for the React frontend (e.g. https://aks-health.contoso.com).",
    )
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    """Return the singleton ApiSettings (cached after first call)."""
    return ApiSettings()  # type: ignore[call-arg]
