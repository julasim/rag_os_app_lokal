"""
OAuth 2.1 + PKCE für den MCP-Endpunkt.

Authorization-Code-Flow mit PKCE (S256), Refresh-Token-Rotation, JWT-Access-Tokens.

Sauber integriert (2026-07-13):
  - Config **nur** über settings() (kein os.environ).
  - Identität = echte UiUser (Login gegen ui_users, siehe oauth_routes.py).
  - Storage = Postgres (OAuthClient + OAuthRefreshToken); Auth-Codes in-memory.
  - iss/aud werden korrekt aus settings().rag_domain gesetzt (Token↔Discovery
    konsistent) — sonst lehnen echte Clients (Claude.ai) den Token ab.

Aktiv, sobald `OAUTH_ENABLED` (default true) + ein Secret vorhanden ist; das
Secret fällt auf `app_secret_key` zurück → out-of-the-box an.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import delete, func, select, update

from config import settings
from db.models import OAuthClient, OAuthRefreshToken
from db.session import get_session
from logger import log

_ALG = "HS256"

# Deckel gegen DCR-Spam (RFC 7591 register ist unauthentifiziert).
MAX_CLIENTS = 500


def is_enabled() -> bool:
    return settings().oauth_active


# ---------------------------------------------------------------------------
# In-Memory Auth-Code-Store (TTL 60s, single-use)
# ---------------------------------------------------------------------------
_auth_codes: dict[str, dict] = {}
_AUTH_CODE_TTL = 60


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------
def _verify_pkce(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, challenge)


# ---------------------------------------------------------------------------
# Client-Registrierung (RFC 7591 DCR) — Postgres
# ---------------------------------------------------------------------------
@dataclass
class ClientInfo:
    client_id: str
    redirect_uris: list[str]
    token_endpoint_auth_method: str


async def count_clients() -> int:
    async with get_session() as s:
        return int(await s.scalar(select(func.count()).select_from(OAuthClient)) or 0)


async def register_client(redirect_uris: list[str]) -> ClientInfo:
    client_id = "mcp-" + secrets.token_urlsafe(16)
    async with get_session() as s:
        s.add(OAuthClient(
            client_id=client_id,
            redirect_uris=redirect_uris,
            token_endpoint_auth_method="none",
        ))
    log.info("oauth.client_registered", client_id=client_id, redirect_uris=redirect_uris)
    return ClientInfo(client_id, redirect_uris, "none")


async def get_client(client_id: str) -> ClientInfo | None:
    async with get_session() as s:
        row = (await s.execute(
            select(OAuthClient).where(OAuthClient.client_id == client_id)
        )).scalar_one_or_none()
    if not row:
        return None
    return ClientInfo(row.client_id, list(row.redirect_uris or []), row.token_endpoint_auth_method)


def _validate_redirect_uri(client: ClientInfo, uri: str) -> bool:
    if uri not in client.redirect_uris:
        return False
    return uri.startswith("https://") or uri.startswith("http://localhost") or uri.startswith("http://127.0.0.1")


# ---------------------------------------------------------------------------
# Auth-Code-Flow (in-memory)
# ---------------------------------------------------------------------------
def issue_auth_code(
    client_id: str, redirect_uri: str, code_challenge: str, scope: str, subject: str
) -> str:
    code = secrets.token_urlsafe(32)
    _auth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "scope": scope,
        "subject": subject,
        "issued_at": time.time(),
    }
    # Opportunistisches Pruning abgelaufener Codes (klein, single-instance).
    if len(_auth_codes) > 256:
        now = time.time()
        for c in [c for c, e in _auth_codes.items() if now - e["issued_at"] > _AUTH_CODE_TTL]:
            _auth_codes.pop(c, None)
    return code


def consume_auth_code(code: str, client_id: str, redirect_uri: str, code_verifier: str) -> dict | None:
    """Single-use Auth-Code einlösen; Claims (subject/scope) zurück oder None."""
    entry = _auth_codes.pop(code, None)
    if not entry:
        return None
    if time.time() - entry["issued_at"] > _AUTH_CODE_TTL:
        return None
    if entry["client_id"] != client_id:
        return None
    if entry["redirect_uri"] != redirect_uri:
        return None
    if not _verify_pkce(code_verifier, entry["code_challenge"]):
        return None
    return {"subject": entry["subject"], "scope": entry["scope"], "client_id": client_id}


# ---------------------------------------------------------------------------
# Token-Ausstellung
# ---------------------------------------------------------------------------
def issue_access_token(subject: str, client_id: str, scope: str) -> str:
    cfg = settings()
    now = int(time.time())
    payload = {
        "iss": cfg.oauth_issuer,
        "aud": cfg.oauth_resource,
        "sub": subject,
        "client_id": client_id,
        "scope": scope,
        "iat": now,
        "exp": now + cfg.oauth_access_ttl,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, cfg.oauth_secret, algorithm=_ALG)


async def issue_refresh_token(subject: str, client_id: str, scope: str) -> str:
    token_id = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    async with get_session() as s:
        s.add(OAuthRefreshToken(
            token_id=token_id,
            client_id=client_id,
            subject=subject,
            scope=scope,
            expires_at=now + timedelta(seconds=settings().oauth_refresh_ttl),
        ))
    return token_id


async def rotate_refresh_token(old_token: str, client_id: str) -> dict | None:
    """
    Tauscht Refresh-Token gegen einen neuen (atomar in einer Transaktion).
    Replay-Detection: wurde der alte bereits ersetzt/revoked → ALLE Tokens des
    Subjects widerrufen (Diebstahl-Annahme).
    """
    now = datetime.now(timezone.utc)
    async with get_session() as s:
        row = (await s.execute(
            select(OAuthRefreshToken).where(OAuthRefreshToken.token_id == old_token)
        )).scalar_one_or_none()
        if not row:
            return None
        if row.revoked:
            await s.execute(
                update(OAuthRefreshToken)
                .where(OAuthRefreshToken.subject == row.subject)
                .values(revoked=True)
            )
            log.warning("oauth.refresh_replay", subject=row.subject, client_id=client_id)
            return None
        if row.expires_at < now:
            return None
        if row.client_id != client_id:
            return None

        new_id = secrets.token_urlsafe(48)
        row.revoked = True
        row.replaced_by = new_id
        subject, scope = row.subject, row.scope
        s.add(OAuthRefreshToken(
            token_id=new_id,
            client_id=client_id,
            subject=subject,
            scope=scope,
            expires_at=now + timedelta(seconds=settings().oauth_refresh_ttl),
        ))
    log.info("oauth.refresh_rotated", subject=subject, client_id=client_id)
    return {"subject": subject, "scope": scope, "client_id": client_id, "new_token": new_id}


async def revoke_token(token: str) -> None:
    async with get_session() as s:
        await s.execute(
            update(OAuthRefreshToken).where(OAuthRefreshToken.token_id == token).values(revoked=True)
        )


# ---------------------------------------------------------------------------
# JWT-Verify
# ---------------------------------------------------------------------------
def verify_access_token(token: str) -> dict | None:
    """Verifiziert Access-JWT (Signatur + iss + aud). Claims oder None."""
    cfg = settings()
    if not cfg.oauth_active:
        return None
    try:
        return jwt.decode(
            token,
            cfg.oauth_secret,
            algorithms=[_ALG],
            audience=cfg.oauth_resource,
            issuer=cfg.oauth_issuer,
        )
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Wartung (Nachtlauf): abgelaufene/revoked Tokens + verwaiste Clients
# ---------------------------------------------------------------------------
async def cleanup_expired() -> int:
    now = datetime.now(timezone.utc)
    async with get_session() as s:
        res = await s.execute(
            delete(OAuthRefreshToken).where(
                (OAuthRefreshToken.expires_at < now) | (OAuthRefreshToken.revoked.is_(True))
            )
        )
    return res.rowcount or 0
