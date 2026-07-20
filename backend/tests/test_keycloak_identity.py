"""The Keycloak Admin provider (app/services/keycloak_identity.py, #51).

Exercised against a MockTransport standing in for the realm — the request shapes and the mapping
of Keycloak's responses to the two errors the caller handles, without a running Keycloak. The one
property that matters most: the created user carries the clinic_id attribute it was handed, which
is what later becomes the tenant claim.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.identity import IdentityError, IdentityUnavailable, UsernameTaken
from app.services.keycloak_identity import KeycloakIdentityProvider

REALM = "luxmedicine"
USER_URL = f"http://kc/admin/realms/{REALM}/users/user-abc"


def _provider(handler) -> KeycloakIdentityProvider:
    return KeycloakIdentityProvider(
        base_url="http://kc",
        realm=REALM,
        client_id="luxmedicine-admin",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
    )


def _ok_handler(record: list[httpx.Request] | None = None):
    """A realm that says yes to everything, recording requests if asked."""

    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        path = request.url.path
        if path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "admin-token"})
        if path.endswith("/users") and request.method == "POST":
            return httpx.Response(201, headers={"Location": USER_URL})
        if "/roles/" in path and request.method == "GET":
            return httpx.Response(200, json={"id": "role-uuid", "name": "clinician"})
        if path.endswith("/role-mappings/realm") and request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(500, text=f"unexpected {request.method} {path}")

    return handler


class TestCreateUser:
    async def test_it_creates_the_user_and_returns_the_keycloak_id(self):
        requests: list[httpx.Request] = []
        idp = _provider(_ok_handler(requests))
        sub = await idp.create_user(
            username="dr.new", password="pw", email="e@x.io", clinic_id="clinic-x", name="Ada Smith"
        )
        assert sub == "user-abc"  # parsed from the Location header

        # The create payload must carry the clinic as a user attribute — the tenant boundary.
        create = next(r for r in requests if r.url.path.endswith("/users") and r.method == "POST")
        payload = json.loads(create.content)
        assert payload["attributes"]["clinic_id"] == ["clinic-x"]
        assert payload["credentials"][0]["value"] == "pw"
        assert payload["email"] == "e@x.io" and payload["emailVerified"] is True
        assert payload["firstName"] == "Ada" and payload["lastName"] == "Smith"

    async def test_it_authenticates_as_the_service_account_first(self):
        requests: list[httpx.Request] = []
        idp = _provider(_ok_handler(requests))
        await idp.create_user(username="u", password="pw", email="e@x.io", clinic_id="c")
        token_req = requests[0]
        assert token_req.url.path.endswith("/protocol/openid-connect/token")
        assert b"grant_type=client_credentials" in token_req.content

    async def test_a_role_is_assigned_when_given(self):
        requests: list[httpx.Request] = []
        idp = _provider(_ok_handler(requests))
        await idp.create_user(username="u", password="pw", email="e@x.io", clinic_id="c", role="clinician")
        assert any(r.url.path.endswith("/role-mappings/realm") for r in requests)

    async def test_no_role_call_when_none_given(self):
        requests: list[httpx.Request] = []
        idp = _provider(_ok_handler(requests))
        await idp.create_user(username="u", password="pw", email="e@x.io", clinic_id="c")
        assert not any("/role-mappings/" in r.url.path for r in requests)


class TestErrorMapping:
    async def test_a_409_is_a_taken_username(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/protocol/openid-connect/token"):
                return httpx.Response(200, json={"access_token": "t"})
            return httpx.Response(409, text="user exists")

        with pytest.raises(UsernameTaken):
            await _provider(handler).create_user(username="taken", password="pw", email="e@x.io", clinic_id="c")

    async def test_a_network_error_is_unavailable_not_a_crash(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(IdentityUnavailable):
            await _provider(handler).create_user(username="u", password="pw", email="e@x.io", clinic_id="c")

    async def test_a_bad_admin_credential_is_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="invalid client")

        with pytest.raises(IdentityUnavailable):
            await _provider(handler).create_user(username="u", password="pw", email="e@x.io", clinic_id="c")

    async def test_a_missing_role_fails_before_creating_the_user(self):
        # The orphan bug the first end-to-end run hit: a role that does not exist must fail with
        # no user created, not after.
        created: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/protocol/openid-connect/token"):
                return httpx.Response(200, json={"access_token": "t"})
            if "/roles/" in request.url.path and request.method == "GET":
                return httpx.Response(404, text="no such role")
            if request.url.path.endswith("/users") and request.method == "POST":
                created.append("user")
                return httpx.Response(201, headers={"Location": USER_URL})
            return httpx.Response(500)

        with pytest.raises(IdentityError):
            await _provider(handler).create_user(
                username="u", password="pw", email="e@x.io", clinic_id="c", role="ghost"
            )
        assert created == []  # no user was created before the role check failed

    async def test_an_unexpected_create_status_surfaces(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/protocol/openid-connect/token"):
                return httpx.Response(200, json={"access_token": "t"})
            return httpx.Response(500, text="boom")

        with pytest.raises(IdentityError):
            await _provider(handler).create_user(username="u", password="pw", email="e@x.io", clinic_id="c")
