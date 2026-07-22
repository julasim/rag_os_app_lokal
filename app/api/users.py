"""
Benutzer-Verwaltung (Track E6) — nur für Web-UI-Admins (`require_ui_admin`).

Legt Mehrbenutzer-Konten an und pflegt Rolle (admin|user) + per-User-Ordner-ACL
(`access_all` vs. `allowed_folders`). **Fail-safe:** ein neuer User sieht per
Default nichts (access_all=false, keine Ordner) — die ACL wird bewusst gesetzt.

Guards gegen Selbst-Aussperrung: man kann sich nicht selbst löschen, und der
letzte verbleibende Admin kann weder gelöscht noch zum User herabgestuft werden.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from auth.dependencies import AuthContext, require_ui_admin
from auth.folders import normalize_folder
from auth.users import hash_password
from db.models import UiUser, UserRole
from db.session import get_local_session
from logger import log

router = APIRouter(prefix="/api/users", tags=["users"])

_ROLES = {UserRole.ADMIN.value, UserRole.USER.value}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class UserAdminResponse(BaseModel):
    id: UUID
    email: str
    role: str
    access_all: bool
    allowed_folders: list[str]
    created_at: datetime
    last_login: datetime | None


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    role: str = UserRole.USER.value
    access_all: bool = False
    allowed_folders: list[str] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=255)
    role: str | None = None
    access_all: bool | None = None
    allowed_folders: list[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_resp(u: UiUser) -> UserAdminResponse:
    return UserAdminResponse(
        id=u.id,
        email=u.email,
        role=u.role,
        access_all=u.access_all,
        allowed_folders=list(u.allowed_folders or []),
        created_at=u.created_at,
        last_login=u.last_login,
    )


def _resolve_acl(access_all: bool, allowed_folders: list[str]) -> tuple[bool, list[str]]:
    """access_all und allowed_folders schließen sich aus: bei access_all=True wird
    die Ordnerliste geleert (unrestricted); sonst kanonisch normalisiert."""
    if access_all:
        return True, []
    return False, sorted({normalize_folder(f) for f in allowed_folders})


async def _admin_count(s) -> int:
    return int(
        await s.scalar(
            select(func.count()).select_from(UiUser).where(UiUser.role == UserRole.ADMIN.value)
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------
@router.get("", response_model=list[UserAdminResponse])
async def list_users(_: AuthContext = Depends(require_ui_admin)):
    async with get_local_session() as s:
        rows = (await s.execute(select(UiUser).order_by(UiUser.created_at))).scalars().all()
    return [_to_resp(u) for u in rows]


@router.post("", response_model=UserAdminResponse, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreateRequest, _: AuthContext = Depends(require_ui_admin)):
    if payload.role not in _ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültige Rolle: {payload.role}")
    email = payload.email.strip().lower()
    access_all, folders = _resolve_acl(payload.access_all, payload.allowed_folders)
    async with get_local_session() as s:
        exists = await s.scalar(select(UiUser.id).where(func.lower(UiUser.email) == email))
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, "E-Mail bereits vergeben")
        u = UiUser(
            email=email,
            password_hash=hash_password(payload.password),
            role=payload.role,
            access_all=access_all,
            allowed_folders=folders,
        )
        s.add(u)
        await s.flush()
        await s.refresh(u)
        resp = _to_resp(u)
    log.info("users.created", email=email, role=payload.role, access_all=access_all)
    return resp


@router.patch("/{user_id}", response_model=UserAdminResponse)
async def update_user(
    user_id: UUID, payload: UserUpdateRequest, _: AuthContext = Depends(require_ui_admin)
):
    if payload.role is not None and payload.role not in _ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültige Rolle: {payload.role}")
    async with get_local_session() as s:
        u = (await s.execute(select(UiUser).where(UiUser.id == user_id))).scalar_one_or_none()
        if not u:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User nicht gefunden")

        # Letzten Admin nicht zum User herabstufen (Selbst-Aussperr-Schutz).
        if (
            payload.role == UserRole.USER.value
            and u.role == UserRole.ADMIN.value
            and await _admin_count(s) <= 1
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Der letzte Admin darf nicht herabgestuft werden")

        if payload.role is not None:
            u.role = payload.role
        if payload.password is not None:
            u.password_hash = hash_password(payload.password)
        # ACL: access_all und/oder allowed_folders konsistent setzen.
        if payload.access_all is not None or payload.allowed_folders is not None:
            new_access_all = payload.access_all if payload.access_all is not None else u.access_all
            new_folders = payload.allowed_folders if payload.allowed_folders is not None else list(u.allowed_folders or [])
            u.access_all, u.allowed_folders = _resolve_acl(new_access_all, new_folders)

        await s.flush()
        await s.refresh(u)
        resp = _to_resp(u)
    log.info("users.updated", user_id=str(user_id))
    return resp


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, ctx: AuthContext = Depends(require_ui_admin)):
    if ctx.ui_user and ctx.ui_user.id == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Man kann sich nicht selbst löschen")
    async with get_local_session() as s:
        u = (await s.execute(select(UiUser).where(UiUser.id == user_id))).scalar_one_or_none()
        if not u:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User nicht gefunden")
        if u.role == UserRole.ADMIN.value and await _admin_count(s) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Der letzte Admin darf nicht gelöscht werden")
        await s.delete(u)
    log.info("users.deleted", user_id=str(user_id))
