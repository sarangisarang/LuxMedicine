# LuxMedicine

A **Clinical Search & Retrieval Engine** for clinical guidelines. A clinician asks a
question; the system returns source-attributed extracts naming the exact edition and
page they came from, and flags where two issuing organisations diverge.

It retrieves and cites. **It does not diagnose, recommend, or advise** — see below.

- **Status:** Phase 0 complete — schema and audit substrate. No ingestion or retrieval yet.
- **Plan:** [docs/ROADMAP.md](docs/ROADMAP.md) — every step is a numbered issue
- **Backend:** [backend/README.md](backend/README.md) — setup and design decisions

## Why the positioning is a technical fact, not a disclaimer

A system that generates treatment recommendations for a specific patient is plausibly
a medical device under EU MDR (Class IIa) — notified body, clinical evaluation, QMS.
That is not a burden a small team clears on the way to a PoC.

So the line is enforced in code. [`app/schemas/answer.py`](backend/app/schemas/answer.py)
has no field for a recommendation, an assessment, or a suggested action. The only
clinical text that can leave the system is a verbatim `quote` carried by a `Citation`.
Conflicts are shown, never resolved — picking a winner would be the advice we avoid.

That sentence was false until #19 was built. The schema also carried a model-written
`statement.text`, with citations attached beneath it as evidence — so a fabricated claim
could ship wearing a genuine quote, which is worse than a bare hallucination because the
citation is what makes it credible. Statements are gone; a test now fails if any field
holding free clinical text reappears.

This turns out to pay for itself technically. Because answers are extractive, a
fabricated quote is *mechanically detectable*: it will not appear in the source text.
A substring check catches it, with no model call and no judgement involved. Free-form
generation forfeits that entirely.

If a feature cannot be expressed in that schema without adding *what the system
advises*, the answer is not to widen the schema. It is to re-open the MDR question.

## Two design decisions worth knowing up front

**Chunks belong to guideline *versions*, not guidelines.** One join, and every answer
resolves to an exact edition and page years later. Superseded editions are archived,
never deleted, so a 2024 answer stays reproducible after the 2026 edition lands.

**GDPR erasure is redaction, not deletion.** A clinician's question is pseudo-personal
data and must be erasable — but deleting it would force an `UPDATE` on the append-only
audit log, which the database rejects. So the text is nulled and its hash kept: the
content is gone, and the trail still proves which question was asked, by whom, against
which sources.

## Stack

FastAPI · PostgreSQL 17 + pgvector · SQLAlchemy 2 · Alembic · Next.js (Phase 8)
