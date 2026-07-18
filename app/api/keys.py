"""API-Key-Management (nur für eingeloggte Admins)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.schemas import ApiKeyCreatedResponse, ApiKeyResponse, CreateApiKeyRequest
from auth.dependencies import AuthContext, require_ui_admin
from auth.keys import create_api_key, list_api_keys, revoke_api_key

router = APIRouter(prefix="/api/keys", tags=["api-keys"])


def _to_response(row) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=row.id,
        label=row.label,
        allowed_folders=row.allowed_folders,
        scopes=row.scopes,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_keys(_: AuthContext = Depends(require_ui_admin)):
    rows = await list_api_keys()
    return [_to_response(r) for r in rows]


@router.post("", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_key(
    payload: CreateApiKeyRequest,
    ctx: AuthContext = Depends(require_ui_admin),
):
    plain, record = await create_api_key(
        label=payload.label,
        allowed_folders=payload.allowed_folders,
        scopes=payload.scopes,
        expires_at=payload.expires_at,
        created_by=ctx.ui_user.id if ctx.ui_user else None,
    )
    base = _to_response(record)
    return ApiKeyCreatedResponse(**base.model_dump(), plain_key=plain)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(key_id: UUID, _: AuthContext = Depends(require_ui_admin)):
    ok = await revoke_api_key(key_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
