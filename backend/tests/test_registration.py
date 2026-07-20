"""Registration by invite (app/services/registration.py, #51).

The orchestration bar: a valid code creates exactly one user, bound to the invite's clinic and
to no other; an unknown, expired, or already-used code creates no user at all; and a provider
outage surfaces rather than silently burning a code. The clinic a user lands in comes from the
invite, never from anything the caller passes — that is the property the whole invite flow
exists to guarantee.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.identity import FakeIdentityProvider, IdentityUnavailable, UsernameTaken
from app.services.invites import (
    InviteAlreadyConsumed,
    InviteExpired,
    InviteNotFound,
    create_invite,
)
from app.services.registration import register_with_invite

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=7)


async def _mint(session, *, clinic_id="clinic-a", expires_at=LATER, role=None):
    _, code = await create_invite(
        session, clinic_id=clinic_id, created_by="admin-1", expires_at=expires_at, role=role
    )
    return code


class TestRegister:
    async def test_a_valid_code_creates_a_user_in_the_invites_clinic(self, session):
        idp = FakeIdentityProvider()
        code = await _mint(session, clinic_id="clinic-north", role="clinician")
        sub = await register_with_invite(
            session, idp, code=code, username="dr.new", password="pw", email="e@x.io", name="Dr New", now=NOW
        )
        assert sub == "kc-dr.new"
        # The user is bound to the INVITE's clinic and role — not anything the caller chose.
        assert idp.users["dr.new"] == {
            "clinic_id": "clinic-north", "role": "clinician", "name": "Dr New", "email": "e@x.io"
        }

    async def test_an_unknown_code_creates_no_user(self, session):
        idp = FakeIdentityProvider()
        with pytest.raises(InviteNotFound):
            await register_with_invite(
                session, idp, code="nope", username="dr.x", password="pw", email="e@x.io", now=NOW
            )
        assert idp.users == {}  # the provider was never called

    async def test_a_code_registers_exactly_one_user(self, session):
        idp = FakeIdentityProvider()
        code = await _mint(session)
        await register_with_invite(
            session, idp, code=code, username="first", password="pw", email="e@x.io", now=NOW
        )
        with pytest.raises(InviteAlreadyConsumed):
            await register_with_invite(
                session, idp, code=code, username="second", password="pw", email="e@x.io", now=NOW
            )
        assert list(idp.users) == ["first"]  # the second never got created

    async def test_an_expired_code_creates_no_user(self, session):
        idp = FakeIdentityProvider()
        code = await _mint(session, expires_at=NOW - timedelta(minutes=1))
        with pytest.raises(InviteExpired):
            await register_with_invite(
                session, idp, code=code, username="dr.late", password="pw", email="e@x.io", now=NOW
            )
        assert idp.users == {}

    async def test_a_provider_outage_surfaces_and_creates_no_user(self, session):
        # The invite is redeemed first, then the provider is unreachable — the caller must roll
        # back (un-spending the code). Here we assert the outage propagates and no user exists;
        # the transaction rollback is exercised at the endpoint level.
        idp = FakeIdentityProvider(unavailable=True)
        code = await _mint(session)
        with pytest.raises(IdentityUnavailable):
            await register_with_invite(
                session, idp, code=code, username="dr.new", password="pw", email="e@x.io", now=NOW
            )
        assert idp.users == {}

    async def test_a_username_collision_surfaces(self, session):
        idp = FakeIdentityProvider()
        idp.users["taken"] = {"clinic_id": "elsewhere", "role": None, "name": None}
        code = await _mint(session)
        with pytest.raises(UsernameTaken):
            await register_with_invite(
                session, idp, code=code, username="taken", password="pw", email="e@x.io", now=NOW
            )
