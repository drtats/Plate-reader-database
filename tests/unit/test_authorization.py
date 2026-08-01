from __future__ import annotations

from dataclasses import dataclass

import pytest

from plate_reader.application.contracts import Actor, Role, UserId
from plate_reader.application.services.authorization import AuthorizationError, require_role

ACTOR = Actor(UserId("user-1"), "user@example.invalid", Role.EDITOR)


@dataclass
class UserLookupStub:
    user: dict[str, object] | None

    def user_by_email(self, email: str) -> dict[str, object] | None:
        assert email == ACTOR.email
        return self.user


def stored_user() -> dict[str, object]:
    return {
        "user_id": "user-1",
        "email": ACTOR.email,
        "role": "editor",
        "is_active": 1,
    }


def test_authorization_accepts_matching_active_stored_role() -> None:
    assert require_role(UserLookupStub(stored_user()), ACTOR, {Role.EDITOR, Role.ADMIN}) == "user-1"


def test_authorization_rejects_command_role_before_lookup() -> None:
    with pytest.raises(AuthorizationError, match="requires one of"):
        require_role(UserLookupStub(stored_user()), ACTOR, {Role.ADMIN})


@pytest.mark.parametrize(
    ("stored", "message"),
    (
        (None, "not registered"),
        ({**stored_user(), "is_active": 0}, "inactive"),
        ({**stored_user(), "user_id": "another-user"}, "does not match"),
        ({**stored_user(), "role": "viewer"}, "Stored role"),
    ),
)
def test_authorization_rejects_invalid_stored_identity(
    stored: dict[str, object] | None, message: str
) -> None:
    with pytest.raises(AuthorizationError, match=message):
        require_role(UserLookupStub(stored), ACTOR, {Role.EDITOR, Role.ADMIN})
