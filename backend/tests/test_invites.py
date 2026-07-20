"""The invite lifecycle (app/services/invites.py, #51).

The bar, set before the code and taken straight from the issue: registration with no valid code
fails; a code lands the user in exactly the invite's clinic; a code works once; an expired or
already-used code fails. Each is a test here, because the whole point of an invite is to be the
one thing that can open a tenant boundary — it has to be exactly right.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.invite import Invite
from app.services.invites import (
    InviteAlreadyConsumed,
    InviteExpired,
    InviteNotFound,
    create_invite,
    generate_code,
    hash_code,
    redeem_invite,
)

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=7)


async def _mint(session, *, clinic_id="clinic-a", expires_at=LATER, role=None):
    return await create_invite(
        session, clinic_id=clinic_id, created_by="admin-1", expires_at=expires_at, role=role
    )


class TestCreate:
    async def test_the_raw_code_is_returned_but_only_its_hash_is_stored(self, session):
        invite, code = await _mint(session)
        assert code  # the raw code exists here, once
        assert invite.code_hash == hash_code(code)
        # The row must not carry the raw code anywhere — a DB leak exposes no usable invite.
        row = (
            await session.execute(select(Invite).where(Invite.id == invite.id))
        ).scalar_one()
        assert code not in (row.code_hash, row.clinic_id, row.created_by)

    async def test_codes_are_high_entropy_and_distinct(self, session):
        codes = {generate_code() for _ in range(200)}
        assert len(codes) == 200  # no collisions across 200 mints
        assert all(len(c) >= 40 for c in codes)  # ~256 bits, url-safe

    async def test_an_invite_must_name_a_clinic(self, session):
        # An invite with no clinic_id would create a tenant-less user auth.py then locks out.
        with pytest.raises(ValueError):
            await _mint(session, clinic_id="   ")


class TestRedeem:
    async def test_a_valid_code_redeems_into_exactly_its_clinic(self, session):
        _, code = await _mint(session, clinic_id="clinic-x")
        redeemed = await redeem_invite(session, code=code, consumed_by="user-9", now=NOW)
        assert redeemed.clinic_id == "clinic-x"  # the user is bound to the invite's clinic
        assert redeemed.consumed_by == "user-9"
        assert redeemed.consumed_at == NOW

    async def test_an_unknown_code_is_refused(self, session):
        with pytest.raises(InviteNotFound):
            await redeem_invite(session, code="not-a-real-code", consumed_by="u", now=NOW)

    async def test_a_code_works_exactly_once(self, session):
        _, code = await _mint(session)
        await redeem_invite(session, code=code, consumed_by="first", now=NOW)
        with pytest.raises(InviteAlreadyConsumed):
            await redeem_invite(session, code=code, consumed_by="second", now=NOW)

    async def test_an_expired_code_is_refused(self, session):
        _, code = await _mint(session, expires_at=NOW - timedelta(minutes=1))
        with pytest.raises(InviteExpired):
            await redeem_invite(session, code=code, consumed_by="u", now=NOW)

    async def test_expiry_is_checked_against_the_passed_time_not_wall_clock(self, session):
        # The same invite is valid before its expiry and expired after — redemption takes `now`
        # explicitly so this is testable rather than a race against the real clock.
        _, code = await _mint(session, expires_at=NOW + timedelta(hours=1))
        with pytest.raises(InviteExpired):
            await redeem_invite(session, code=code, consumed_by="u", now=NOW + timedelta(hours=2))

    async def test_a_failed_redemption_does_not_consume_the_invite(self, session):
        # Redeeming with the wrong code must not touch a real, still-valid invite.
        invite, _ = await _mint(session)
        with pytest.raises(InviteNotFound):
            await redeem_invite(session, code="wrong", consumed_by="u", now=NOW)
        row = (
            await session.execute(select(Invite).where(Invite.id == invite.id))
        ).scalar_one()
        assert row.consumed_at is None  # still usable

    async def test_an_expired_code_stays_unconsumed(self, session):
        # A refused-because-expired code must not be silently marked consumed.
        invite, code = await _mint(session, expires_at=NOW - timedelta(minutes=1))
        with pytest.raises(InviteExpired):
            await redeem_invite(session, code=code, consumed_by="u", now=NOW)
        row = (
            await session.execute(select(Invite).where(Invite.id == invite.id))
        ).scalar_one()
        assert row.consumed_at is None
