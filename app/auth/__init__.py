"""Auth-Schicht: UI-User + API-Keys."""
from auth.dependencies import (
    AuthContext,
    require_api_key,
    require_ui_user,
    require_ui_admin,
)
from auth.keys import create_api_key, list_api_keys, revoke_api_key, verify_api_key
from auth.users import authenticate_user, ensure_admin_user

__all__ = [
    "AuthContext",
    "authenticate_user",
    "create_api_key",
    "ensure_admin_user",
    "list_api_keys",
    "require_api_key",
    "require_ui_admin",
    "require_ui_user",
    "revoke_api_key",
    "verify_api_key",
]
