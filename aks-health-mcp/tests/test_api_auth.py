"""
Unit tests for the Azure AD auth module.
JWKS fetching and token validation are mocked.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.auth.azure_ad import (
    AuthenticatedUser,
    _check_group_membership,
    _validate_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_ID = "00000000-0000-0000-0000-000000000000"
CLIENT_ID = "11111111-1111-1111-1111-111111111111"
GROUP_ID  = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

SAMPLE_CLAIMS = {
    "oid":   "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "upn":   "admin@contoso.com",
    "name":  "Sysadmin User",
    "email": "admin@contoso.com",
    "groups": [GROUP_ID, "cccccccc-cccc-cccc-cccc-cccccccccccc"],
    "exp":   int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp()),
}


def _make_settings(
    client_id: str = CLIENT_ID,
    tenant_id: str = TENANT_ID,
    group: str = GROUP_ID,
) -> MagicMock:
    s = MagicMock()
    s.azure_tenant_id        = tenant_id
    s.azure_ad_app_client_id = client_id
    s.azure_ad_allowed_group = group
    return s


# ---------------------------------------------------------------------------
# _validate_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("api.auth.azure_ad._get_jwks", new_callable=AsyncMock)
@patch("api.auth.azure_ad.jwt.decode")
async def test_validate_token_success(
    mock_decode: MagicMock,
    mock_jwks: AsyncMock,
) -> None:
    mock_jwks.return_value = {"keys": []}
    mock_decode.return_value = SAMPLE_CLAIMS

    claims = await _validate_token("fake.token.value", _make_settings())
    assert claims["upn"] == "admin@contoso.com"
    mock_decode.assert_called_once()


@pytest.mark.asyncio
@patch("api.auth.azure_ad._get_jwks", new_callable=AsyncMock)
@patch("api.auth.azure_ad.jwt.decode")
async def test_validate_token_expired(
    mock_decode: MagicMock,
    mock_jwks: AsyncMock,
) -> None:
    from jose.exceptions import ExpiredSignatureError
    mock_jwks.return_value = {"keys": []}
    mock_decode.side_effect = ExpiredSignatureError("Token expired")

    with pytest.raises(HTTPException) as exc_info:
        await _validate_token("expired.token", _make_settings())
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


@pytest.mark.asyncio
@patch("api.auth.azure_ad._get_jwks", new_callable=AsyncMock)
@patch("api.auth.azure_ad.jwt.decode")
async def test_validate_token_invalid(
    mock_decode: MagicMock,
    mock_jwks: AsyncMock,
) -> None:
    from jose import JWTError
    mock_jwks.return_value = {"keys": []}
    mock_decode.side_effect = JWTError("Invalid signature")

    with pytest.raises(HTTPException) as exc_info:
        await _validate_token("bad.token", _make_settings())
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# _check_group_membership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_group_authorized() -> None:
    """User with the required group in claims → no exception."""
    await _check_group_membership(SAMPLE_CLAIMS, "token", GROUP_ID)


@pytest.mark.asyncio
async def test_check_group_denied() -> None:
    """User without the required group → HTTP 403."""
    wrong_group = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    with pytest.raises(HTTPException) as exc_info:
        await _check_group_membership(SAMPLE_CLAIMS, "token", wrong_group)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_check_group_no_groups_claim() -> None:
    """Token with no groups claim and no _claim_names → HTTP 403 with hint."""
    claims_no_groups = {k: v for k, v in SAMPLE_CLAIMS.items() if k != "groups"}
    with pytest.raises(HTTPException) as exc_info:
        await _check_group_membership(claims_no_groups, "token", GROUP_ID)
    assert exc_info.value.status_code == 403
    # Should hint about app manifest configuration
    assert "groups claim" in exc_info.value.detail


@pytest.mark.asyncio
@patch("api.auth.azure_ad.httpx.AsyncClient")
async def test_check_group_graph_fallback_authorized(mock_client_cls: MagicMock) -> None:
    """When _claim_names present, Graph API is called as fallback."""
    # Claims with _claim_names (>200 groups scenario) but no groups claim
    claims_with_src = {
        **{k: v for k, v in SAMPLE_CLAIMS.items() if k != "groups"},
        "_claim_names": {"groups": "src1"},
        "_claim_sources": {"src1": {"endpoint": "https://graph.microsoft.com/..."}},
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"value": [{"id": GROUP_ID}]}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=None)
    mock_client.get        = AsyncMock(return_value=mock_resp)
    mock_client_cls.return_value = mock_client

    # Should not raise
    await _check_group_membership(claims_with_src, "token", GROUP_ID)


# ---------------------------------------------------------------------------
# AuthenticatedUser model
# ---------------------------------------------------------------------------


def test_authenticated_user_fields() -> None:
    user = AuthenticatedUser(SAMPLE_CLAIMS)
    assert user.upn   == "admin@contoso.com"
    assert user.name  == "Sysadmin User"
    assert user.email == "admin@contoso.com"
    assert GROUP_ID in user.groups


def test_authenticated_user_fallback_upn() -> None:
    """If 'upn' missing, use 'preferred_username'."""
    claims = {**SAMPLE_CLAIMS}
    del claims["upn"]
    claims["preferred_username"] = "alt@contoso.com"
    user = AuthenticatedUser(claims)
    assert user.upn == "alt@contoso.com"


# ---------------------------------------------------------------------------
# API routes – smoke tests
# ---------------------------------------------------------------------------


def test_healthz_endpoint() -> None:
    from fastapi.testclient import TestClient
    import os
    # Patch required env vars
    os.environ.setdefault("AZURE_TENANT_ID",        TENANT_ID)
    os.environ.setdefault("AZURE_FOUNDRY_ENDPOINT",  "https://t.services.ai.azure.com/m")
    os.environ.setdefault("AZURE_AD_APP_CLIENT_ID",  CLIENT_ID)
    os.environ.setdefault("AZURE_AD_ALLOWED_GROUP",  GROUP_ID)

    from api.main import app
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_me_endpoint_requires_auth() -> None:
    from fastapi.testclient import TestClient
    import os
    os.environ.setdefault("AZURE_TENANT_ID",        TENANT_ID)
    os.environ.setdefault("AZURE_FOUNDRY_ENDPOINT",  "https://t.services.ai.azure.com/m")
    os.environ.setdefault("AZURE_AD_APP_CLIENT_ID",  CLIENT_ID)
    os.environ.setdefault("AZURE_AD_ALLOWED_GROUP",  GROUP_ID)

    from api.main import app
    client = TestClient(app)
    # No Authorization header → 403 (HTTPBearer returns 403 when missing)
    resp = client.get("/api/auth/me")
    assert resp.status_code in (401, 403)
