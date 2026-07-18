"""REST-API-Router."""
from api.auth_router import router as auth_router
from api.documents import router as documents_router
from api.keys import router as keys_router
from api.maintenance import router as maintenance_router
from api.suggest import router as suggest_router
from api.system import router as system_router
from api.users import router as users_router

__all__ = [
    "auth_router",
    "documents_router",
    "keys_router",
    "maintenance_router",
    "suggest_router",
    "system_router",
    "users_router",
]
