"""The identity provider seam — creating a user in Keycloak, behind a protocol (#51).

Registration has to create an account somewhere, and that somewhere is Keycloak. But the
*orchestration* — redeem the invite, create the user with the invite's clinic, undo on failure
— is logic we want tested without a running Keycloak. So it depends on this narrow protocol, and
the real Admin-API implementation (step 3) is one class behind it, exactly like the embedder and
the extractor. Tests and local dev use FakeIdentityProvider; production wires the real one.

The one method matters: `create_user` is handed the `clinic_id` and sets it as the user's
attribute, which is what the token mapper later turns into the claim auth.py reads. The caller
never lets a registrant choose that value — see services/registration.py.
"""

from __future__ import annotations

from typing import Protocol


class IdentityError(Exception):
    """Creating the user did not succeed."""


class UsernameTaken(IdentityError):
    """The username already exists in the provider. A registration collision, not our bug."""


class IdentityUnavailable(IdentityError):
    """The provider could not be reached. Distinct so the caller can leave the invite unspent
    and say 'try again', rather than burning a code on an outage."""


class IdentityProvider(Protocol):
    async def create_user(
        self,
        *,
        username: str,
        password: str,
        email: str,
        clinic_id: str,
        name: str | None = None,
        role: str | None = None,
    ) -> str:
        """Create a user bound to `clinic_id` and return its subject id (the token `sub`).

        `email` is required and set verified: Keycloak refuses a login as "account not fully set
        up" without it, and the admin's invite is the verification. Raises UsernameTaken if the
        username is in use, IdentityUnavailable if the provider is unreachable.
        """
        ...


class FakeIdentityProvider:
    """An in-memory identity provider for tests and local dev — no network, deterministic sub.

    It enforces the two things a caller must handle: a username collides only once, and an
    outage can be simulated so the un-consume-on-failure path is testable.
    """

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        # username -> the attributes it was created with, so a test can assert the clinic bound.
        self.users: dict[str, dict[str, str | None]] = {}

    async def create_user(
        self,
        *,
        username: str,
        password: str,
        email: str,
        clinic_id: str,
        name: str | None = None,
        role: str | None = None,
    ) -> str:
        if self.unavailable:
            raise IdentityUnavailable("identity provider is unreachable")
        if username in self.users:
            raise UsernameTaken(f"username already exists: {username}")
        self.users[username] = {"clinic_id": clinic_id, "role": role, "name": name, "email": email}
        return f"kc-{username}"
