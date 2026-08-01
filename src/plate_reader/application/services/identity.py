"""Map verified OIDC claims to database-backed application roles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from plate_reader.application.contracts import Actor, Role, UserId


class AuthenticationError(PermissionError):
    pass


class IdentityUserRepository(Protocol):
    def user_by_email(self, email: str) -> Mapping[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class OidcClaims:
    subject: str
    email: str
    display_name: str

    @classmethod
    def from_mapping(cls, claims: Mapping[str, object]) -> OidcClaims:
        subject = _claim(claims, "sub")
        email = _claim(claims, "email").casefold()
        display_name = _optional_claim(claims, "name") or email.split("@", maxsplit=1)[0]
        if "@" not in email:
            raise AuthenticationError("OIDC email claim is invalid")
        return cls(subject, email, display_name)


class ResolveAuthenticatedActorService:
    def __init__(self, repository: IdentityUserRepository) -> None:
        self.repository = repository

    def execute(self, claims: OidcClaims) -> Actor:
        stored = self.repository.user_by_email(claims.email)
        if stored is None:
            raise AuthenticationError("Authenticated user is not authorized for this application")
        if not bool(stored["is_active"]):
            raise AuthenticationError("Authenticated user is inactive")
        try:
            role = Role(str(stored["role"]))
        except ValueError as error:
            raise AuthenticationError("Stored user role is invalid") from error
        return Actor(UserId(str(stored["user_id"])), claims.email, role)


def _claim(claims: Mapping[str, object], key: str) -> str:
    value = claims.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuthenticationError(f"OIDC {key} claim is required")
    return value.strip()


def _optional_claim(claims: Mapping[str, object], key: str) -> str | None:
    value = claims.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuthenticationError(f"OIDC {key} claim must be text")
    return value.strip() or None
