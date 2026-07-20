"""The real identity provider: create a user through Keycloak's Admin REST API (#51).

Implements the IdentityProvider protocol that services/registration.py depends on. It acts as a
confidential service account (client_credentials) holding `manage-users`, and does exactly one
thing the orchestration asks: create a user whose `clinic_id` attribute is the invite's, so the
realm's mapper later turns it into the claim auth.py reads. Nothing here decides a clinic — it is
handed one.

Kept behind the protocol so registration is testable without a running Keycloak; here the HTTP is
real, and its own failures are mapped to the two the caller knows how to handle: a 409 is a taken
username (a collision, not our fault), and anything that means "could not reach or complete the
call" is IdentityUnavailable, so /register leaves the invite unspent and says "try again" rather
than burning a code on an outage.
"""

from __future__ import annotations

import httpx

from app.services.identity import IdentityError, IdentityUnavailable, UsernameTaken

# Keycloak's Admin API is chatty; these calls are small. A short timeout keeps a hung realm from
# hanging a registration request.
_TIMEOUT = 10.0


class KeycloakIdentityProvider:
    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        # Injected only in tests (httpx.MockTransport); None in production means real network.
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT, transport=self._transport
        )

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
        try:
            async with self._client() as client:
                token = await self._admin_token(client)
                auth = {"Authorization": f"Bearer {token}"}
                # Resolve the role BEFORE creating anything: a role that does not exist must fail
                # here, with no user left behind. Doing it after create_user is how the first
                # end-to-end run left an orphan account and 500'd.
                role_repr = await self._lookup_role(client, auth, role) if role else None
                user_id = await self._create(client, auth, username, password, email, clinic_id, name)
                if role_repr is not None:
                    try:
                        await self._assign_role(client, auth, user_id, role_repr)
                    except Exception:
                        # The account exists but the role would not attach. Undo the create so a
                        # retry is clean rather than colliding on the username.
                        await self._delete_user_best_effort(client, auth, user_id)
                        raise
                return user_id
        except httpx.RequestError as exc:
            # DNS, connect, timeout, read — the realm could not be reached or did not finish.
            raise IdentityUnavailable(f"could not reach the identity provider: {exc}") from exc

    async def _admin_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            f"/realms/{self._realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if resp.status_code != 200:
            # Bad service-account credentials or a realm that will not issue — an outage from
            # the caller's point of view, and never a reason to spend the invite.
            raise IdentityUnavailable(f"admin token request failed: {resp.status_code}")
        return resp.json()["access_token"]

    async def _create(
        self,
        client: httpx.AsyncClient,
        auth: dict[str, str],
        username: str,
        password: str,
        email: str,
        clinic_id: str,
        name: str | None,
    ) -> str:
        payload: dict[str, object] = {
            "username": username,
            "email": email,
            "enabled": True,
            # Redeeming an admin's invite IS the verification — without a verified email Keycloak
            # reports "Account is not fully set up" and refuses the login. (Keycloak 26's user
            # profile also requires the email to be present, which is why it is not optional.)
            "emailVerified": True,
            # The whole point: the tenant travels as a user attribute the realm mapper reads.
            "attributes": {"clinic_id": [clinic_id]},
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        }
        if name:
            first, _, last = name.strip().partition(" ")
            payload["firstName"] = first
            if last:
                payload["lastName"] = last

        resp = await client.post(f"/admin/realms/{self._realm}/users", json=payload, headers=auth)
        if resp.status_code == 409:
            raise UsernameTaken(f"username already exists: {username}")
        if resp.status_code != 201:
            raise IdentityError(f"user creation failed: {resp.status_code} {resp.text[:200]}")

        # Keycloak returns the new user's id only in the Location header — it IS the token `sub`.
        location = resp.headers.get("Location", "")
        user_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not user_id:
            raise IdentityError("user was created but Keycloak returned no id")
        return user_id

    async def _lookup_role(
        self, client: httpx.AsyncClient, auth: dict[str, str], role: str
    ) -> dict:
        """Resolve a realm role to its representation, before any user is created. A 404 is a
        misconfigured invite (a role that does not exist), surfaced with nothing left behind."""
        got = await client.get(f"/admin/realms/{self._realm}/roles/{role}", headers=auth)
        if got.status_code == 404:
            raise IdentityError(f"role {role!r} does not exist in the realm")
        if got.status_code != 200:
            raise IdentityError(f"could not look up role {role!r}: {got.status_code}")
        return got.json()

    async def _assign_role(
        self, client: httpx.AsyncClient, auth: dict[str, str], user_id: str, role_repr: dict
    ) -> None:
        assigned = await client.post(
            f"/admin/realms/{self._realm}/users/{user_id}/role-mappings/realm",
            json=[{"id": role_repr["id"], "name": role_repr["name"]}],
            headers=auth,
        )
        if assigned.status_code not in (204, 200):
            raise IdentityError(f"role assignment failed: {assigned.status_code}")

    async def _delete_user_best_effort(
        self, client: httpx.AsyncClient, auth: dict[str, str], user_id: str
    ) -> None:
        """Undo a create whose role could not be attached. Best effort: if the delete itself
        fails, the original error is what matters and still propagates."""
        try:
            await client.delete(f"/admin/realms/{self._realm}/users/{user_id}", headers=auth)
        except httpx.RequestError:
            pass
