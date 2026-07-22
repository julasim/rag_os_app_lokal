"""
API-Key-Verwaltung.

Design:
  - Beim Erstellen wird der Klartext-Key EINMAL zurückgegeben ("rag_sk_...")
  - Gespeichert wird nur der bcrypt-Hash
  - Verifikation erfolgt durch Probieren aller unverfallenen Keys
    (effizient genug für bis zu ~1000 Keys; wenn das je zum Problem wird,
    nehmen wir einen Key-Prefix + Lookup-Index)
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID

import bcrypt
from sqlalchemy import delete, select, update

from db.models import ApiKey, Scope
from db.session import get_local_session

_KEY_PREFIX = "rag_sk_"
# Länge des gespeicherten Lookup-Prefix (rag_sk_ = 7 + 9 Token-Zeichen). Grenzt
# die bcrypt-Kandidaten auf ~1 ein; der Rest des Keys (~34 Zeichen) bleibt geheim.
_PREFIX_LEN = 16


# ---------------------------------------------------------------------------
# Erstellen
# ---------------------------------------------------------------------------
async def create_api_key(
    label: str,
    allowed_folders: list[str],
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
    created_by: UUID | None = None,
) -> tuple[str, ApiKey]:
    """
    Erzeugt einen neuen API-Key. Gibt den Klartext-Key und das DB-Objekt zurück.
    Der Klartext-Key wird NUR hier zurückgegeben — danach nie wieder lesbar.
    """
    plain = _KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    record = ApiKey(
        key_hash=key_hash,
        key_prefix=plain[:_PREFIX_LEN],   # M2: Lookup-Index für verify_api_key
        label=label,
        allowed_folders=allowed_folders,
        scopes=scopes or [Scope.READ.value],
        expires_at=expires_at,
        created_by=created_by,
    )
    async with get_local_session() as s:
        s.add(record)
        await s.flush()
        await s.refresh(record)
    return plain, record


# ---------------------------------------------------------------------------
# Verifizieren
# ---------------------------------------------------------------------------
async def verify_api_key(plain_key: str) -> ApiKey | None:
    """
    Findet den passenden Key-Datensatz, wenn der Klartext-Key gültig ist.
    Aktualisiert last_used_at.
    """
    if not plain_key or not plain_key.startswith(_KEY_PREFIX):
        return None

    async with get_local_session() as s:
        now = datetime.now(timezone.utc)
        # M2: nur Keys mit passendem Prefix (oder Bestands-Keys ohne Prefix) laden,
        # statt ALLE per bcrypt zu probieren. Der Prefix trifft normalerweise genau
        # einen Key; NULL-Prefix-Keys (vor M2 angelegt) bleiben als Fallback dabei.
        prefix = plain_key[:_PREFIX_LEN]
        stmt = (
            select(ApiKey)
            .where((ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now))
            .where((ApiKey.key_prefix == prefix) | (ApiKey.key_prefix.is_(None)))
        )
        result = await s.execute(stmt)
        candidates = result.scalars().all()

        for row in candidates:
            if bcrypt.checkpw(plain_key.encode("utf-8"), row.key_hash.encode("utf-8")):
                await s.execute(
                    update(ApiKey).where(ApiKey.id == row.id).values(last_used_at=now)
                )
                return row
        return None


# ---------------------------------------------------------------------------
# Auflisten / Widerrufen
# ---------------------------------------------------------------------------
async def list_api_keys() -> list[ApiKey]:
    async with get_local_session() as s:
        result = await s.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
        return list(result.scalars().all())


async def revoke_api_key(key_id: UUID) -> bool:
    async with get_local_session() as s:
        result = await s.execute(delete(ApiKey).where(ApiKey.id == key_id))
        return result.rowcount > 0
