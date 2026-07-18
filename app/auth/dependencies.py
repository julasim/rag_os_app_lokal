"""
FastAPI-Dependencies für Authentifizierung + Autorisierung.

  - require_api_key     : schützt REST-/MCP-Endpunkte (für Programme)
  - require_ui_user     : schützt UI-Endpunkte (für Menschen, per JWT-Cookie)
  - require_ui_admin    : schärfere Variante, nur role=admin
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select

from auth.folders import key_allows_folder, user_allows_folder
from auth.keys import verify_api_key
from auth.users import decode_session_token
from config import settings
from db.models import ApiKey, UiUser, UserRole
from db.session import get_session


async def _local_admin() -> UiUser | None:
    """Lokaler Desktop-Modus (127.0.0.1, Ein-Nutzer): UI-Endpunkte ohne Token
    fallen auf den lokalen Admin zurück. None, wenn deaktiviert oder kein Admin."""
    if not settings().local_ui_autologin:
        return None
    async with get_session() as s:
        return (
            await s.execute(
                select(UiUser)
                .where(UiUser.email == settings().admin_email)
                .limit(1)
            )
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Kontext-Objekt, das in Endpoints injiziert wird
# ---------------------------------------------------------------------------
@dataclass
class AuthContext:
    api_key: ApiKey | None = None
    ui_user: UiUser | None = None

    @property
    def is_api(self) -> bool:
        return self.api_key is not None

    @property
    def is_ui(self) -> bool:
        return self.ui_user is not None

    @property
    def is_admin(self) -> bool:
        """Nur echte Admin-UI-User. API-Keys/OAuth sind nie „Admin" im UI-Sinn."""
        return self.ui_user is not None and self.ui_user.role == UserRole.ADMIN.value

    def can_access_folder(self, folder_path: str) -> bool:
        """Prüft ob dieser Auth-Kontext Zugriff auf den Ordner hat.

        Track E — fail-safe:
          - UI-User: Admin **oder** `access_all` → Vollzugriff; sonst greift die
            per-User-ACL (`user_allows_folder`, leere allowed_folders = NICHTS).
          - API-Key: Bearer-Semantik (`key_allows_folder`, leer = alles).
        """
        if self.ui_user:
            if self.ui_user.role == UserRole.ADMIN.value or self.ui_user.access_all:
                return True
            return user_allows_folder(
                self.ui_user.access_all, self.ui_user.allowed_folders, folder_path
            )
        if self.api_key:
            return key_allows_folder(self.api_key.allowed_folders, folder_path)
        return False

    def has_scope(self, scope: str) -> bool:
        """Track E — UI-Rollen-Gating: Admin hat alle Scopes, `role=user` nur
        `read`. API-Keys nach ihrer eigenen Scope-Liste."""
        if self.ui_user:
            if self.ui_user.role == UserRole.ADMIN.value:
                return True
            return scope == "read"
        if self.api_key:
            return scope in self.api_key.scopes
        return False


# ---------------------------------------------------------------------------
# API-Key (via Authorization-Header)
# ---------------------------------------------------------------------------
async def require_api_key(
    authorization: str | None = Header(None),
) -> AuthContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    key = await verify_api_key(token)
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return AuthContext(api_key=key)


# ---------------------------------------------------------------------------
# UI-User (via JWT-Cookie oder Authorization-Header für Streamlit-Backend)
# ---------------------------------------------------------------------------
async def require_ui_user(
    session_token: str | None = Cookie(None, alias="rag_session"),
    x_ui_token: str | None = Header(None, alias="X-UI-Token"),
) -> AuthContext:
    token = session_token or x_ui_token
    if not token:
        local = await _local_admin()
        if local:
            return AuthContext(ui_user=local)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    payload = decode_session_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")

    async with get_session() as s:
        result = await s.execute(select(UiUser).where(UiUser.id == payload["sub"]))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return AuthContext(ui_user=user)


async def require_ui_admin(
    ctx: AuthContext = Depends(require_ui_user),
) -> AuthContext:
    if ctx.ui_user and ctx.ui_user.role != UserRole.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return ctx


# ---------------------------------------------------------------------------
# Kombiniert: Entweder API-Key ODER UI-Session
# ---------------------------------------------------------------------------
async def require_any_auth(
    authorization: str | None = Header(None),
    session_token: str | None = Cookie(None, alias="rag_session"),
    x_ui_token: str | None = Header(None, alias="X-UI-Token"),
) -> AuthContext:
    # API-Key hat Vorrang
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        key = await verify_api_key(token)
        if key:
            return AuthContext(api_key=key)

    # Fallback: UI-Session
    ui_token = session_token or x_ui_token
    if ui_token:
        payload = decode_session_token(ui_token)
        if payload:
            async with get_session() as s:
                result = await s.execute(
                    select(UiUser).where(UiUser.id == payload["sub"])
                )
                user = result.scalar_one_or_none()
                if user:
                    return AuthContext(ui_user=user)

    # Lokaler Desktop-Modus: ohne jede Auth auf den lokalen Admin zurückfallen.
    local = await _local_admin()
    if local:
        return AuthContext(ui_user=local)

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
