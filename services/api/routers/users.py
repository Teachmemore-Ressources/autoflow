# Autoflow — routers/users — Apache 2.0
"""
User management endpoints  (admin-only).

Endpoints
---------
GET    /users                       — List all users (username + role)
POST   /users                       — Create a new user
DELETE /users/{username}            — Delete a user
PUT    /users/{username}/role       — Change a user's role
PUT    /users/{username}/password   — Change a user's password
"""

from __future__ import annotations

import user_store
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from routers.auth import require_auth

router = APIRouter(prefix="/users", tags=["User Management"])


# ── Request / response models ─────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-\.]+$")
    password: str = Field(..., min_length=8)
    role: str = Field(..., description="One of: admin, operator, viewer")


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., description="One of: admin, operator, viewer")


class UpdatePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8)


# ── Admin guard ───────────────────────────────────────────────────────────────


async def require_admin(username: str = Depends(require_auth)) -> str:
    """Raise 403 unless the caller has the admin role."""
    role = user_store.get_role(username)
    # X-API-Key callers are always treated as admin (backward compatibility)
    if role is None or role == "admin":
        return username
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Admin role required — your role is '{role}'",
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", summary="List all users")
async def list_users(caller: str = Depends(require_admin)):
    """Return a list of ``{username, role}`` objects.  Passwords are never returned."""
    return {"users": user_store.list_users()}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a new user")
async def create_user(body: CreateUserRequest, caller: str = Depends(require_admin)):
    """
    Create a new user.

    **Roles**: ``admin`` | ``operator`` | ``viewer``

    Passwords must be at least 8 characters.
    """
    try:
        user_store.create_user(body.username, body.password, body.role)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return {"status": "created", "username": body.username, "role": body.role}


@router.delete("/{username}", summary="Delete a user")
async def delete_user(username: str, caller: str = Depends(require_admin)):
    """
    Delete a user.

    - Raises **404** if the user does not exist.
    - Raises **409** if the user is the last admin (lockout prevention).
    - Raises **403** if a caller tries to delete themselves (use ``PUT /password``
      to reset credentials, then ask another admin to delete the account).
    """
    if username == caller:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete your own account",
        )
    try:
        user_store.delete_user(username)
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_409_CONFLICT if "last admin" in detail else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=code, detail=detail)
    return {"status": "deleted", "username": username}


@router.put("/{username}/role", summary="Change a user's role")
async def update_role(
    username: str,
    body: UpdateRoleRequest,
    caller: str = Depends(require_admin),
):
    """
    Change a user's role.  Raises **409** if the change would remove the last admin.
    """
    try:
        user_store.update_role(username, body.role)  # type: ignore[arg-type]
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=detail)
    return {"status": "updated", "username": username, "role": body.role}


@router.put("/{username}/password", summary="Change a user's password")
async def update_password(
    username: str,
    body: UpdatePasswordRequest,
    caller: str = Depends(require_admin),
):
    """Change a user's password.  Minimum 8 characters required."""
    try:
        user_store.update_password(username, body.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"status": "updated", "username": username}
