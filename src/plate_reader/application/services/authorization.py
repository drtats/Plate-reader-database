"""Shared application authorization checks."""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Protocol

from plate_reader.application.contracts import Actor, Role


class AuthorizationError(PermissionError):
    pass


class UserLookup(Protocol):
    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...


def require_role(repository: UserLookup, actor: Actor, allowed: Set[Role]) -> str:
    if actor.role not in allowed:
        raise AuthorizationError(f"This operation requires one of: {_role_names(allowed)}")
    stored = repository.user_by_email(actor.email)
    if stored is None:
        raise AuthorizationError("Authenticated user is not registered")
    if not bool(stored["is_active"]):
        raise AuthorizationError("Authenticated user is inactive")
    if str(stored["user_id"]) != actor.user_id:
        raise AuthorizationError("Authenticated identity does not match stored user")
    stored_role = Role(str(stored["role"]))
    if stored_role not in allowed:
        raise AuthorizationError(f"Stored role requires one of: {_role_names(allowed)}")
    return str(stored["user_id"])


def _role_names(roles: Set[Role]) -> str:
    return ", ".join(sorted(role.value for role in roles))
