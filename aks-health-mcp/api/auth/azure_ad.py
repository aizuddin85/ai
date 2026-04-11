"""
Azure AD JWT validation and group-membership guard.

Token validation flow
---------------------
1. Extract the Bearer token from the Authorization header.
2. Decode the JWT header to get the key-ID (kid).
3. Fetch (and cache for 1 hour) the JWKS from Azure AD's discovery endpoint.
4. Verify the signature, expiry, issuer, and audience.
5. Check that the required AD group OID appears in the 'groups' claim.

Over-200-groups fallback
------------------------
If the user is a member of more than 200 groups, Azure AD omits the
'groups' claim and sets:
  "_claim_names": {"groups": "src1"}
  "_claim_sources": {"src1": {"endpoint": "<graph-url>"}}

In that scenario this module calls Microsoft Graph on behalf of the user
(using their bearer token, which must have GroupMember.Read.All delegated
permission) to perform a transitive memberOf check.

The app registration manifest must include:
  "groupMembershipClaims": "SecurityGroup"
and optionally:
  "optionalClaims": { "idToken": [{"name": "groups", ...}] }
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
# Group membership check
# ---------------------------------------------------------------------------


async def _check_group_membership(
    claims: dict[str, Any],
    token: str,
    required_group: str,
) -> None:
    """
    Verify the user belongs to the required AD group.

    Primary path: inspect the 'groups' claim in the JWT.
    Fallback path: call Microsoft Graph transitiveMemberOf when the groups
                   claim is absent (user in >200 groups).

    Raises HTTP 403 if not a member.
    """
    groups_in_token: list[str] | None = claims.get("groups")

    if groups_in_token is not None:
        if required_group in groups_in_token:
            logger.info(
                "auth.group.authorized",
                upn=claims.get("upn") or claims.get("preferred_username"),
                group=required_group,
            )
            return
        _deny(claims, required_group)

    # ── Fallback: groups claim absent → call Graph API ───────────────
    # This requires the delegated permission GroupMember.Read.All on the
    # frontend app registration.
    if "_claim_names" in claims and "groups" in claims.get("_claim_names", {}):
        logger.info("auth.group.graph_fallback", oid=claims.get("oid"))
        await _graph_check(token, claims.get("oid", ""), required_group, claims)
        return

    # No groups claim and no _claim_names → deny (configuration issue)
    logger.warning(
        "auth.group.no_groups_claim",
        oid=claims.get("oid"),
        hint="Add 'groupMembershipClaims': 'SecurityGroup' to the app manifest",
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Token contains no groups claim. "
            "Configure the app registration to emit group claims "
            "(Token configuration → Add groups claim → Security groups)."
        ),
    )


async def _graph_check(
    bearer_token: str,
    user_oid: str,
    required_group: str,
    claims: dict[str, Any],
) -> None:
    """Call MS Graph /me/transitiveMemberOf to check group membership."""
    url = "https://graph.microsoft.com/v1.0/me/transitiveMemberOf/microsoft.graph.group"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {"$select": "id", "$top": "999"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 403:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Cannot verify group membership via Microsoft Graph. "
                        "Ensure the app has GroupMember.Read.All delegated permission."
                    ),
                )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.error("auth.graph.error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify group membership via Microsoft Graph",
        ) from exc

    group_ids = {g["id"] for g in data.get("value", [])}
    if required_group in group_ids:
        logger.info(
            "auth.group.authorized_via_graph",
            upn=claims.get("upn") or claims.get("preferred_username"),
            group=required_group,
        )
        return

    _deny(claims, required_group)


def _deny(claims: dict[str, Any], group: str) -> None:
    logger.warning(
        "auth.group.denied",
        upn=claims.get("upn") or claims.get("preferred_username"),
        oid=claims.get("oid"),
        required_group=group,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not a member of the authorised group for this application.",
    )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


class AuthenticatedUser:
    """Carries verified identity information about the authenticated caller."""

    def __init__(self, claims: dict[str, Any]) -> None:
        self.oid: str = claims.get("oid", "")
        self.upn: str = (
            claims.get("upn") or claims.get("preferred_username") or "unknown"
        )
        self.name: str = claims.get("name", self.upn)
        self.email: str = claims.get("email") or self.upn
        self.groups: list[str] = claims.get("groups", [])
        self.raw_claims = claims


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    settings: ApiSettings = Depends(get_api_settings),
) -> AuthenticatedUser:
    """
    FastAPI dependency that:
      1. Validates the Azure AD bearer token.
      2. Asserts group membership.
      3. Returns the authenticated user.

    Inject with:  user: AuthenticatedUser = Depends(get_current_user)
    """
    token = credentials.credentials
    claims = await _validate_token(token, settings)
    await _check_group_membership(claims, token, settings.azure_ad_allowed_group)
    return AuthenticatedUser(claims)
