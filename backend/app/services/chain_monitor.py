"""Scheduled chain verification and checkpointing (#29).

The chain detects tampering only if something walks it. Unscheduled, it proves nothing —
which is what #29 says. Building it turned up the reason that mattered more:

**`GET /audit/verify` returned HTTP 200 for a broken chain.** Every generic monitor —
`curl -f`, an uptime check, a Kubernetes probe, a cron job checking exit status — reads
200 as healthy. The endpoint that exists to detect tampering reported tampering as
success. Scheduling it in that state would have produced a green dashboard over a
rewritten trail, which is worse than not checking at all: it manufactures confidence.

**What is code and what is not.** The cron entry, the alert routing, the pager — those are
deployment. This provides what they need: a check that fails loudly, an exit code, and a
checkpoint worth shipping somewhere.

**Checkpoints and the limit of same-database evidence.** A checkpoint turns "the chain is
broken" into "the chain broke after T". That bound is only as good as the checkpoint, and
a checkpoint here is only as good as this database — anyone who can rewrite audit_log can
rewrite chain_checkpoints. 0009's append-only trigger means two guards instead of one; it
does not mean tamper-proof. `export_checkpoint()` exists so an operator can copy one
somewhere this system cannot reach. That copy is the evidence; this table is a
convenience.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.checkpoint import ChainCheckpoint
from app.services.audit import ChainBreak, verify_chain


@dataclass(frozen=True)
class VerificationResult:
    intact: bool
    entries_verified: int = 0

    broken_at_seq: int | None = None
    reason: str | None = None

    checkpoint_id: int | None = None
    verified_through_seq: int | None = None
    tail_row_hash: str | None = None

    # The last checkpoint before this run. On a break, this is the answer to "when": the
    # chain was intact at that moment, so the damage happened since.
    previous_checkpoint_at: datetime | None = None

    @property
    def break_is_bounded(self) -> bool:
        """Whether we can say *when* a break happened, rather than only that it did."""
        return not self.intact and self.previous_checkpoint_at is not None


async def latest_checkpoint(session: AsyncSession) -> ChainCheckpoint | None:
    return (
        await session.execute(
            select(ChainCheckpoint).order_by(ChainCheckpoint.id.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def verify_and_checkpoint(
    session: AsyncSession, *, verified_by: str = "scheduled"
) -> VerificationResult:
    """Verify the whole chain and, if intact, record a checkpoint. Commits.

    **From genesis every time, deliberately.** Verifying only from the last checkpoint
    forward would be faster and would trust the checkpoint to vouch for everything below
    it — which is exactly what an attacker who rewrote both tables would want. The check
    that costs O(n) is the check that is worth running; if that becomes too slow, the
    answer is an external checkpoint that can be trusted, not a cheaper local one that
    cannot.
    """
    previous = await latest_checkpoint(session)
    previous_at = previous.verified_at if previous else None

    try:
        entries = await verify_chain(session)
    except ChainBreak as exc:
        # No checkpoint on a break. A checkpoint asserts the chain was sound; writing one
        # now would assert something false, and it is the one record that must not.
        return VerificationResult(
            intact=False,
            broken_at_seq=exc.seq,
            reason=exc.reason,
            previous_checkpoint_at=previous_at,
        )

    tail = (
        await session.execute(select(AuditLog).order_by(AuditLog.seq.desc()).limit(1))
    ).scalar_one_or_none()

    if tail is None:
        # An empty chain is intact and there is nothing to checkpoint. Recording one would
        # be a claim about a chain that does not exist.
        return VerificationResult(intact=True, entries_verified=0, previous_checkpoint_at=previous_at)

    checkpoint = ChainCheckpoint(
        verified_through_seq=tail.seq,
        tail_row_hash=tail.row_hash,
        entries_verified=entries,
        verified_at=datetime.now(UTC),
        verified_by=verified_by,
    )
    session.add(checkpoint)
    await session.commit()

    return VerificationResult(
        intact=True,
        entries_verified=entries,
        checkpoint_id=checkpoint.id,
        verified_through_seq=checkpoint.verified_through_seq,
        tail_row_hash=checkpoint.tail_row_hash,
        previous_checkpoint_at=previous_at,
    )


def export_checkpoint(result: VerificationResult) -> str:
    """One line, for copying somewhere this system cannot reach.

    This is the part that makes a checkpoint evidence rather than a note-to-self. Append
    it to a WORM bucket, a managed append-only log, a printout — anywhere whose integrity
    does not depend on the database it describes. Without that step the bound on a break
    is asserted by the same system that is under suspicion.
    """
    if not result.intact:
        return (
            f"CHAIN BROKEN at seq={result.broken_at_seq}: {result.reason}"
            + (
                f" (intact as of {result.previous_checkpoint_at.isoformat()})"
                if result.previous_checkpoint_at
                else " (no prior checkpoint — the break cannot be bounded in time)"
            )
        )
    if result.verified_through_seq is None:
        return "chain empty, nothing to checkpoint"
    return (
        f"chain intact through seq={result.verified_through_seq} "
        f"tail={result.tail_row_hash} entries={result.entries_verified}"
    )
