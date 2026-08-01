from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.application.services import (
    AuthenticationError,
    OidcClaims,
    ResolveAuthenticatedActorService,
)


@dataclass
class Users:
    stored: dict[str, object] | None

    def user_by_email(self, email: str) -> dict[str, object] | None:
        assert email == "scientist@example.invalid"
        return self.stored


def test_oidc_claims_map_to_database_role_without_claim_role_elevation() -> None:
    claims = OidcClaims.from_mapping(
        {
            "sub": "provider-subject",
            "email": "Scientist@Example.Invalid",
            "name": "Scientist",
            "role": "admin",
        }
    )
    actor = ResolveAuthenticatedActorService(
        Users(
            {
                "user_id": "database-user",
                "email": claims.email,
                "role": "viewer",
                "is_active": 1,
            }
        )
    ).execute(claims)

    assert actor == Actor(UserId("database-user"), "scientist@example.invalid", Role.VIEWER)


def test_oidc_name_defaults_to_email_prefix() -> None:
    claims = OidcClaims.from_mapping(
        {"sub": "provider-subject", "email": "Scientist@Example.Invalid"}
    )
    assert claims.display_name == "scientist"


def test_microsoft_username_fallback_and_temporal_claim_validation() -> None:
    claims = OidcClaims.from_mapping(
        {
            "sub": "provider-subject",
            "preferred_username": "Scientist@Example.Invalid",
            "exp": int(datetime.now(UTC).timestamp()) + 60,
        }
    )
    assert claims.email == "scientist@example.invalid"
    assert claims.expires_at is not None


@pytest.mark.parametrize(
    "claims",
    (
        {
            "sub": "x",
            "email": "scientist@example.invalid",
            "email_verified": False,
        },
        {
            "sub": "x",
            "email": "scientist@example.invalid",
            "email_verified": "false",
        },
        {
            "sub": "x",
            "email": "scientist@example.invalid",
            "exp": 1,
        },
        {
            "sub": "x",
            "email": "scientist@example.invalid",
            "exp": "tomorrow",
        },
    ),
)
def test_unverified_expired_or_malformed_oidc_claims_are_rejected(
    claims: dict[str, object],
) -> None:
    with pytest.raises(AuthenticationError):
        OidcClaims.from_mapping(claims)


def test_hosted_oidc_claims_require_expiration() -> None:
    with pytest.raises(AuthenticationError, match="exp claim"):
        OidcClaims.from_mapping(
            {"sub": "x", "email": "scientist@example.invalid"},
            require_expiration=True,
        )


@pytest.mark.parametrize(
    "claims",
    (
        {},
        {"sub": "x"},
        {"sub": "x", "email": "invalid"},
        {"sub": "x", "email": "scientist@example.invalid", "name": 123},
    ),
)
def test_invalid_oidc_claims_are_rejected(claims: dict[str, object]) -> None:
    with pytest.raises(AuthenticationError):
        OidcClaims.from_mapping(claims)


@pytest.mark.parametrize(
    ("stored", "message"),
    (
        (None, "not authorized"),
        (
            {"user_id": "user", "role": "editor", "is_active": 0},
            "inactive",
        ),
        (
            {"user_id": "user", "role": "owner", "is_active": 1},
            "role is invalid",
        ),
    ),
)
def test_database_authorization_failures_are_rejected(
    stored: dict[str, object] | None, message: str
) -> None:
    claims = OidcClaims("subject", "scientist@example.invalid", "Scientist")
    with pytest.raises(AuthenticationError, match=message):
        ResolveAuthenticatedActorService(Users(stored)).execute(claims)
