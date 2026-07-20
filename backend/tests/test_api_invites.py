"""The registration endpoints (app/api/invites.py, #51).

These pin the trust boundary end to end over HTTP: /invites refuses anyone but a clinic-admin
and scopes the invite to that admin's own clinic; /register takes no token, accepts only a
valid code, lands the user in the invite's clinic, and — the property that needs a real
request to prove — rolls the whole thing back so a code is never spent unless the account was
actually created.
"""

from __future__ import annotations

import contextlib

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.invites import get_identity_provider
from app.core.auth import Clinician, current_clinician
from app.db.session import get_session
from app.services.identity import FakeIdentityProvider


@contextlib.asynccontextmanager
async def _client(session, *, clinician=None, idp=None):
    """An HTTP client over the app with the session, identity, and (optionally) the caller's
    identity overridden — the standard pattern in test_auth, so no real Keycloak is needed."""
    from app.main import app

    async def _session():
        yield session

    app.dependency_overrides[get_session] = _session
    if idp is not None:
        app.dependency_overrides[get_identity_provider] = lambda: idp
    if clinician is not None:
        app.dependency_overrides[current_clinician] = lambda: clinician
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _admin(clinic="clinic-north"):
    return Clinician(actor_id="admin-1", clinic_id=clinic, roles=frozenset({"clinic-admin"}))


def _plain(clinic="clinic-north"):
    return Clinician(actor_id="dr-1", clinic_id=clinic, roles=frozenset())


class TestMintInvite:
    async def test_a_non_admin_is_forbidden(self, session):
        async with _client(session, clinician=_plain()) as client:
            r = await client.post("/invites", json={})
        assert r.status_code == 403

    async def test_no_token_is_unauthorized(self, session):
        # current_clinician NOT overridden — the real dependency runs and rejects the missing
        # bearer, so /invites is never reachable without authentication.
        async with _client(session) as client:
            r = await client.post("/invites", json={})
        assert r.status_code == 401

    async def test_an_admin_mints_a_code_scoped_to_their_own_clinic(self, session):
        async with _client(session, clinician=_admin("clinic-south")) as client:
            r = await client.post("/invites", json={"role": "clinician", "ttl_hours": 48})
        assert r.status_code == 201
        body = r.json()
        assert body["code"]  # the raw code, once
        assert body["clinic_id"] == "clinic-south"  # the admin's clinic, not a body field
        assert body["role"] == "clinician"


class TestRegister:
    async def _mint(self, session, clinic="clinic-x"):
        async with _client(session, clinician=_admin(clinic)) as client:
            r = await client.post("/invites", json={})
        return r.json()["code"]

    async def test_a_valid_code_creates_a_user_in_the_invites_clinic(self, session):
        code = await self._mint(session, clinic="clinic-x")
        idp = FakeIdentityProvider()
        async with _client(session, idp=idp) as client:
            r = await client.post(
                "/register",
                json={"code": code, "username": "dr.new", "password": "pw", "name": "Dr New"},
            )
        assert r.status_code == 201
        assert r.json()["user_id"] == "kc-dr.new"
        assert idp.users["dr.new"]["clinic_id"] == "clinic-x"

    async def test_a_bad_code_is_a_generic_400(self, session):
        idp = FakeIdentityProvider()
        async with _client(session, idp=idp) as client:
            r = await client.post(
                "/register", json={"code": "nope", "username": "u", "password": "pw"}
            )
        assert r.status_code == 400
        assert "invalid or expired" in r.json()["detail"]  # never says "no such code"
        assert idp.users == {}

    async def test_a_code_cannot_be_used_twice(self, session):
        code = await self._mint(session)
        idp = FakeIdentityProvider()
        async with _client(session, idp=idp) as client:
            first = await client.post(
                "/register", json={"code": code, "username": "first", "password": "pw"}
            )
            second = await client.post(
                "/register", json={"code": code, "username": "second", "password": "pw"}
            )
        assert first.status_code == 201
        assert second.status_code == 400
        assert list(idp.users) == ["first"]

    async def test_a_provider_outage_leaves_the_code_spendable(self, session):
        # The rollback property, only provable over a real request: registration fails with the
        # provider down, but the SAME code then succeeds once the provider is back. If the failed
        # attempt had consumed the invite, the retry would 400 instead.
        code = await self._mint(session)
        down = FakeIdentityProvider(unavailable=True)
        async with _client(session, idp=down) as client:
            failed = await client.post(
                "/register", json={"code": code, "username": "dr.new", "password": "pw"}
            )
        assert failed.status_code == 503

        up = FakeIdentityProvider()
        async with _client(session, idp=up) as client:
            retry = await client.post(
                "/register", json={"code": code, "username": "dr.new", "password": "pw"}
            )
        assert retry.status_code == 201  # the code was NOT burned by the outage
        assert "dr.new" in up.users
