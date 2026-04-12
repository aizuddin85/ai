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
    _JWKS_CACHE_MAX_SIZE,
    _JWKS_TTL,
    _get_jwks,
    _jwks_cache,
    _validate_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT_ID = "00000000-0000-0000-0000-000000000000"
CLIENT_ID = "11111111-1111-1111-1111-111111111111"

SAMPLE_CLAIMS = {
    "oid":   "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "upn":   "admin@contoso.com",
    "name":  "Sysadmin User",
    "email": "admin@contoso.com",
    "groups": ["cccccccc-cccc-cccc-cccc-cccccccccccc"],
    "exp":   int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp()),
}


def _make_settings(
    client_id: str = CLIENT_ID,
    tenant_id: str = TENANT_ID,
) -> MagicMock:
    s = MagicMock()
    s.azure_tenant_id        = tenant_id
    s.azure_ad_app_client_id = client_id
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
# AuthenticatedUser model
# ---------------------------------------------------------------------------


def test_authenticated_user_fields() -> None:
    user = AuthenticatedUser(SAMPLE_CLAIMS, access_token="tok")
    assert user.upn          == "admin@contoso.com"
    assert user.name         == "Sysadmin User"
    assert user.email        == "admin@contoso.com"
    assert user.access_token == "tok"


def test_authenticated_user_fallback_upn() -> None:
    """If 'upn' missing, use 'preferred_username'."""
    claims = {**SAMPLE_CLAIMS}
    del claims["upn"]
    claims["preferred_username"] = "alt@contoso.com"
    user = AuthenticatedUser(claims)
    assert user.upn == "alt@contoso.com"


def test_authenticated_user_no_group_check() -> None:
    """Users with no groups claim are allowed through (no group gate)."""
    claims_no_groups = {k: v for k, v in SAMPLE_CLAIMS.items() if k != "groups"}
    user = AuthenticatedUser(claims_no_groups)
    # Should not raise — groups are optional; empty list is fine
    assert user.groups == []


# ---------------------------------------------------------------------------
# API routes – smoke tests
# ---------------------------------------------------------------------------


def test_healthz_endpoint() -> None:
    from fastapi.testclient import TestClient
    import os
    os.environ.setdefault("AZURE_TENANT_ID",        TENANT_ID)
    os.environ.setdefault("AZURE_FOUNDRY_ENDPOINT",  "https://t.services.ai.azure.com/m")
    os.environ.setdefault("AZURE_AD_APP_CLIENT_ID",  CLIENT_ID)

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

    from api.main import app
    client = TestClient(app)
    # No Authorization header → 403 (HTTPBearer returns 403 when missing)
    resp = client.get("/api/auth/me")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# JWKS cache — session isolation guarantees
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_jwks_cache() -> None:  # type: ignore[return]
    """Wipe the module-level JWKS cache before and after each test."""
    _jwks_cache.clear()
    yield
    _jwks_cache.clear()


@pytest.mark.asyncio
async def test_jwks_cache_hit_avoids_network() -> None:
    """A warm cache entry within TTL must not trigger a network fetch."""
    _jwks_cache["tenant-a"] = (time.time(), {"keys": [{"kid": "k1"}]})

    with patch("api.auth.azure_ad.httpx.AsyncClient") as mock_http:
        result = await _get_jwks("tenant-a")

    mock_http.assert_not_called()
    assert result["keys"][0]["kid"] == "k1"


@pytest.mark.asyncio
async def test_jwks_cache_miss_fetches_and_stores() -> None:
    """A cold cache triggers a network fetch and stores the result."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"keys": [{"kid": "fresh"}]}
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("api.auth.azure_ad.httpx.AsyncClient", return_value=mock_http):
        result = await _get_jwks("tenant-b")

    assert result["keys"][0]["kid"] == "fresh"
    assert "tenant-b" in _jwks_cache


@pytest.mark.asyncio
async def test_jwks_cache_expired_entry_is_replaced() -> None:
    """An entry older than _JWKS_TTL is treated as a cache miss."""
    old_ts = time.time() - _JWKS_TTL - 1
    _jwks_cache["tenant-c"] = (old_ts, {"keys": [{"kid": "stale"}]})

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"keys": [{"kid": "refreshed"}]}
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("api.auth.azure_ad.httpx.AsyncClient", return_value=mock_http):
        result = await _get_jwks("tenant-c")

    assert result["keys"][0]["kid"] == "refreshed"


@pytest.mark.asyncio
async def test_jwks_cache_bounded_by_max_size() -> None:
    """Cache must never grow beyond _JWKS_CACHE_MAX_SIZE entries."""
    # Fill the cache with _JWKS_CACHE_MAX_SIZE entries (all fresh)
    now = time.time()
    for i in range(_JWKS_CACHE_MAX_SIZE):
        _jwks_cache[f"tenant-fill-{i}"] = (now, {"keys": []})

    assert len(_jwks_cache) == _JWKS_CACHE_MAX_SIZE

    # Adding one more tenant must evict the oldest so size stays bounded
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"keys": []}
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch("api.auth.azure_ad.httpx.AsyncClient", return_value=mock_http):
        await _get_jwks("tenant-overflow")

    assert len(_jwks_cache) == _JWKS_CACHE_MAX_SIZE
    assert "tenant-overflow" in _jwks_cache


@pytest.mark.asyncio
async def test_jwks_cache_isolates_tenants() -> None:
    """Each tenant gets its own JWKS entry — no cross-tenant data mixing."""
    _jwks_cache["tenant-x"] = (time.time(), {"keys": [{"kid": "x-key"}]})
    _jwks_cache["tenant-y"] = (time.time(), {"keys": [{"kid": "y-key"}]})

    with patch("api.auth.azure_ad.httpx.AsyncClient") as mock_http:
        result_x = await _get_jwks("tenant-x")
        result_y = await _get_jwks("tenant-y")

    mock_http.assert_not_called()
    assert result_x["keys"][0]["kid"] == "x-key"
    assert result_y["keys"][0]["kid"] == "y-key"
