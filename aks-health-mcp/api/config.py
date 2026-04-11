"""
API-layer configuration (extends the MCP server settings).

New settings required for the FastAPI backend:

  AZURE_AD_APP_CLIENT_ID   – Client ID of the Azure AD app registration
                             used for frontend SSO (may differ from the MCP
                             server's service-principal client ID).
  AZURE_AD_ALLOWED_GROUP   – Object ID of the AD security group whose
                             members are authorised to use the UI.
  FRONTEND_ORIGIN          – CORS-allowed origin, e.g. https://aks-health.contoso.com
  API_HOST / API_PORT      – Uvicorn bind address (default 0.0.0.0:8000).

The app registration must be configured to emit the 'groups' claim:
  Azure Portal → App Registration → Token configuration →
  Add groups claim → Security groups.
If a user belongs to more than 200 groups Azure AD omits the claim and
sets _claim_names instead; see api/auth/azure_ad.py for the fallback path.
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
    azure_ad_allowed_group: str = Field(
        ...,
        description=(
            "Object ID (GUID) of the Azure AD security group whose members are "
            "authorised to access the UI. Everyone else receives HTTP 403."
        ),
    )

    # ----------------------------------------------------------------
    # CORS / server
    # ----------------------------------------------------------------
    frontend_origin: str = Field(
        default="http://localhost:5173",
        description="Allowed CORS origin for the React frontend.",
    )
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    """Return the singleton ApiSettings (cached after first call)."""
    return ApiSettings()  # type: ignore[call-arg]
