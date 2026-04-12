"""
/api/auth endpoints – identity and authorisation info.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth.azure_ad import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def get_me(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    """
    Return the authenticated user's profile.

    The frontend calls this immediately after login to confirm the token is
    valid.  Any authenticated Azure AD user receives HTTP 200 here.
    Resource-level access is controlled by Azure RBAC at query time.
    """
    return {
        "oid": user.oid,
        "upn": user.upn,
        "name": user.name,
        "email": user.email,
        "authorized": True,
    }
