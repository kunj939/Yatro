from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.providers.registry import Providers, get_providers


def providers_dep() -> Providers:
    return get_providers()


def _token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token(request)
    user_id = decode_access_token(token) if token else None
    if user_id is None:
        raise Unauthorized("Please sign in to continue.")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Please sign in to continue.")
    return user


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    try:
        return current_user(request, db)
    except Unauthorized:
        return None


def is_owner(user: User) -> bool:
    """An owner is someone named in ADMIN_EMAILS (set on the server, never from the console)."""
    return user.email.strip().lower() in get_settings().admin_email_set


def admin_user(user: User | None = Depends(optional_user)) -> User:
    """Operational endpoints and the admin console: the owners (ADMIN_EMAILS) and anyone an owner made an admin in the console.
    Everyone else, signed in or not, gets a plain 404: they are not told it exists."""
    if user is None or not (is_owner(user) or user.is_admin):
        raise NotFound("Not found.")
    return user


def owner_user(user: User = Depends(admin_user)) -> User:
    """The few things only an owner may do (making or removing admins, touching another admin's account)."""
    if not is_owner(user):
        raise Forbidden("Only an owner can do this.", code="owner_only")
    return user
