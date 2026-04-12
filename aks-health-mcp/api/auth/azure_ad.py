"""
Azure AD JWT validation.

Token validation flow
---------------------
1. Extract the Bearer token from the Authorization header.
2. Decode the JWT header to get the key-ID (kid).
3. Fetch (and cache for 1 hour) the JWKS from Azure AD's discovery endpoint.
4. Verify the signature, expiry, issuer, and audience.

Authorization model
-------------------
Any successfully authenticated Azure AD user is allowed access to the API.
Resource-level authorization is enforced downstream through Azure RBAC:
the backend exchanges the user's token for an Azure Resource Manager token
via the On-Behalf-Of (OBO) flow.  Azure then returns only the resources the
user's identity (and their group-based role assignments) can actually access.
Users whose identities have no Azure RBAC roles on a subscription will
receive empty results or 403 errors from Azure — not from this module.

No group claim or AZURE_AD_ALLOWED_GROUP configuration is required.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from api.config import ApiSettings, get_api_settings

logger = structlog.get_logger(__name__)

_bearer = HTTPBearer(auto_error=True)

# ---------------------------------------------------------------------------
# JWKS cache (per tenant)
# ---------------------------------------------------------------------------
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_TTL = 3600  # 1 hour


async def _get_jwks(tenant_id: str) -> dict[str, Any]:
    """
    Fetch (or return cached) JWKS for the given tenant.
    Uses an async httpx client; raises HTTPException on failure.
    """
    cached = _jwks_cache.get(tenant_id)
    if cached and (time.time() - cached[0]) < _JWKS_TTL:
        return cached[1]

    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
    except httpx.HTTPError as exc:
        logger.error("auth.jwks.fetch_failed", url=url, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to reach Azure AD JWKS endpoint",
        ) from exc

    _jwks_cache[tenant_id] = (time.time(), data)
    logger.debug("auth.jwks.refreshed", tenant_id=tenant_id, key_count=len(data.get("keys", [])))
    return data


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------


async def _validate_token(
    token: str,
    settings: ApiSettings,
) -> dict[str, Any]:
    """
    Validate an Azure AD JWT access token.

    Returns the decoded claims dict.
    Raises HTTP 401 on any validation failure.
    """
    tenant_id = settings.azure_tenant_id
    client_id = settings.azure_ad_app_client_id

    jwks = await _get_jwks(tenant_id)

    # Azure AD v2.0 issuer
    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    # Accept both api:// and bare client-ID audiences
    audiences = [client_id, f"api://{client_id}"]

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=audiences,
            issuer=issuer,
            options={"verify_at_hash": False},
        )
    except ExpiredSignatureError as exc:
        logger.warning("auth.token.expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except JWTError as exc:
        logger.warning("auth.token.invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    logger.info(
        "auth.token.valid",
        upn=claims.get("upn") or claims.get("preferred_username"),
        oid=claims.get("oid"),
    )
    return claims


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


class AuthenticatedUser:
    """Carries verified identity information about the authenticated caller."""

    def __init__(self, claims: dict[str, Any], access_token: str = "") -> None:
        self.oid: str = claims.get("oid", "")
        self.upn: str = (
            claims.get("upn") or claims.get("preferred_username") or "unknown"
        )
        self.name: str = claims.get("name", self.upn)
        self.email: str = claims.get("email") or self.upn
        self.groups: list[str] = claims.get("groups", [])
        self.raw_claims = claims
        # Raw bearer token — used downstream for OBO exchange
        self.access_token: str = access_token


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    settings: ApiSettings = Depends(get_api_settings),
) -> AuthenticatedUser:
    """
    FastAPI dependency that validates the Azure AD bearer token and returns
    the authenticated user.

    Authorization is purely based on a valid Azure AD token — no AD group
    membership check is performed here.  Resource access is controlled by
    the user's Azure RBAC role assignments evaluated at query time via OBO.

    Inject with:  user: AuthenticatedUser = Depends(get_current_user)
    """
    token = credentials.credentials
    claims = await _validate_token(token, settings)
    return AuthenticatedUser(claims, access_token=token)
