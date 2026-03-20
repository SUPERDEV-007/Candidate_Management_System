import os
import secrets
from functools import lru_cache
from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, status


ROLE_ADMIN = "admin"
ROLE_RECRUITER = "recruiter"

DEFAULT_ADMIN_KEY = "dev-admin-key"
DEFAULT_RECRUITER_KEY = "dev-recruiter-key"
INSECURE_FALLBACK_ENV = "ALLOW_INSECURE_DEFAULT_KEYS"
ENABLE_AUTH_ENV = "ENABLE_API_KEY_AUTH"


def _parse_keys(value: str) -> set[str]:
    return {key.strip() for key in value.split(",") if key.strip()}


def auth_is_enabled() -> bool:
    return os.getenv(ENABLE_AUTH_ENV, "").strip().lower() in {"1", "true", "yes"}


@lru_cache(maxsize=1)
def _key_registry() -> dict[str, str]:
    if not auth_is_enabled():
        return {}

    admin_env = os.getenv("ADMIN_API_KEYS", "").strip()
    recruiter_env = os.getenv("RECRUITER_API_KEYS", "").strip()
    allow_insecure_defaults = os.getenv(INSECURE_FALLBACK_ENV, "").strip().lower() in {"1", "true", "yes"}

    if not admin_env or not recruiter_env:
        if allow_insecure_defaults:
            admin_env = admin_env or DEFAULT_ADMIN_KEY
            recruiter_env = recruiter_env or DEFAULT_RECRUITER_KEY
        else:
            raise RuntimeError(
                "Auth keys are not configured. Set ADMIN_API_KEYS and RECRUITER_API_KEYS. "
                "For local-only development, you may set ALLOW_INSECURE_DEFAULT_KEYS=true."
            )

    admin_keys = _parse_keys(admin_env)
    recruiter_keys = _parse_keys(recruiter_env)

    if not admin_keys or not recruiter_keys:
        raise RuntimeError(
            "Invalid auth key configuration. ADMIN_API_KEYS and RECRUITER_API_KEYS must contain at least one key each."
        )

    registry: dict[str, str] = {}
    for key in recruiter_keys:
        registry[key] = ROLE_RECRUITER
    for key in admin_keys:
        registry[key] = ROLE_ADMIN
    return registry


def ensure_auth_configured() -> None:
    if auth_is_enabled():
        _key_registry()


def resolve_user_role(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    if not auth_is_enabled():
        # Public/demo mode: allow all write actions without API keys.
        return ROLE_ADMIN

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    registry = _key_registry()
    for configured_key, role in registry.items():
        if secrets.compare_digest(x_api_key, configured_key):
            return role

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )


def require_roles(*allowed_roles: str) -> Callable[[str], str]:
    allowed = set(allowed_roles)

    def _dependency(current_role: str = Depends(resolve_user_role)) -> str:
        if current_role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role permissions",
            )
        return current_role

    return _dependency
