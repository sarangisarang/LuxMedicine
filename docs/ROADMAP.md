# LuxMedicine — Roadmap

Every step below is a GitHub issue. They are ordered by dependency, not by appeal:
each phase unblocks the next, and skipping ahead means rework.

Two rules constrain everything here:

1. **The system retrieves and cites. It does not advise.** Enforced by
   `app/schemas/answer.py`, which has no field for a recommendation. Widening that
   schema is not a refactor — it re-opens the MDR Class IIa question.
2. **The audit trail is evidence, not logging.** Append-only in the database,
   hash-chained on top. Any feature that needs to mutate `audit_log` is designed wrong.

---

## Phase 0 — Foundation ✅ done

The schema and the audit substrate. Chunks belong to *versions*, so every answer
resolves to an exact edition and page years later.

- [x] `documents` / `document_versions` / `chunks` / `queries` / `audit_log`
- [x] Append-only triggers (UPDATE/DELETE + separate TRUNCATE) and role REVOKE
- [x] Hash chain with `pg_advisory_xact_lock` on append
- [x] `AnswerPayload` — the extractive answer contract
- [x] 13 DB-free chain tests

## CI ✅ done

`.github/workflows/ci.yml` — ruff, `alembic upgrade head`, `alembic check`, pytest
against a real pgvector service container, and gitleaks over full history.

Postgres is not mocked and the schema is not built with `create_all()`: the append-only
triggers and the advisory lock exist only in the database, so a run without them would
be green while proving nothing.

**No CD, deliberately.** There is no ingestion, no retrieval, and no environment to
deploy to. A pipeline now would be infrastructure for an application that does not
exist. Revisit when Phase 4 makes something worth running for someone else — at which
point data residency (#6) constrains where it may run.

## Phase 1 — Prove the substrate on a real database

The chain is currently verified by tests that never touch Postgres. The guarantees
that matter most — the trigger, the lock — only exist *in* Postgres.

- **#1** Run `alembic upgrade head` against the live pgvector container
- **#2** Test: `UPDATE audit_log` and `DELETE FROM audit_log` are rejected by the trigger
- **#3** Test: `TRUNCATE audit_log` is rejected (row triggers don't fire on truncate)
- **#4** Test: concurrent appends produce an unforked chain (this is what the advisory lock buys)
- **#5** Test: `redact_query()` clears text while `verify_chain()` still passes — the GDPR/immutability contract
- **#6** Decide the embedding model and dimension **before any ingestion**

> #6 is a one-way door. `vector(1536)` is baked into migration 0001; changing it later
> means re-embedding every chunk. 18-language support points at a multilingual model
> (`multilingual-e5-large`, 1024) rather than the OpenAI default.

## Phase 2 — Ingestion

Nothing downstream exists until chunks do. `issuing_org` is captured here, and
conflict detection depends on it entirely.

- **#7** PDF upload endpoint; `file_hash` (sha256) rejects duplicate ingestion
- **#8** Text extraction with page numbers preserved — page is a citation field, not metadata
- **#9** Section-aware chunking (page + section carried onto every chunk)
- **#10** Embedding generation and batch insert
- **#11** Register `Document` + `DocumentVersion` with `issuing_org` / `region` / `version_label`
- **#12** Supersession: mark the old version `archived`, set `superseded_by`
- **#13** Scanned-PDF path via a Vision model (defer until a real scanned guideline forces it)

## Phase 3 — Retrieval

- **#14** Vector search over `active` versions, with archived reachable by explicit request
- **#15** Hybrid search: pgvector + Postgres full-text, fused
- **#16** Multilingual term expansion — "ჰიპერტენზია" must reach "hypertension" and "Hypertonie"
- **#17** Staleness detection: flag hits whose version has a `superseded_by`

> #15 is not optional polish. Pure vector search misses exact drug names and dosages —
> the highest-stakes tokens in the corpus. Lexical matching is what catches "Enalapril
> 20 mg" when the embedding drifts toward a semantic neighbour.

## Phase 4 — Extractive answering

- **#18** Strict extraction prompt returning `AnswerPayload` as structured output
- **#19** **Verbatim citation validation** — reject any `quote` not found character-for-character in its cited chunk
- **#20** `no_answer_reason` path when the corpus cannot answer
- **#21** Per-statement guarantee: no citations ⇒ the statement never ships

> #19 is the strongest anti-hallucination mechanism available here, and it is a cheap
> substring check rather than a model call. Because the schema is extractive, a
> fabricated quote is *mechanically detectable*: it will not appear in the source text.
> Free-form generation forfeits this — which is one more reason the MDR positioning
> pays for itself technically, not just legally.

## Phase 5 — Conflict detection

- **#22** Group retrieved chunks by `issuing_org`
- **#23** Escalation rule: run the comparison pass only when a result set spans >1 organisation
- **#24** Comparison pass → `ConflictFinding`, showing both passages, resolving neither
- **#25** False-conflict guard: different patient populations are not a conflict

> #25 is where this feature earns or loses trust. "ESC says X for adults, AHA says Y for
> paediatric patients" is not a contradiction, and reporting it as one teaches clinicians
> to ignore the warning — at which point the real conflicts get ignored too.

## Phase 6 — Audit integration

- **#26** Wire `append_audit_entry()` into the query flow — one row per answered query
- **#27** Audit export: reconstruct "who asked what, when, against which edition and page"
- **#28** GDPR erasure endpoint over `redact_query()`
- **#29** Schedule `/audit/verify`; alert on `ChainBreak`

> #29 closes the loop. A hash chain detects tampering only if something walks it.
> Unscheduled, it proves nothing.

## Phase 7 — Identity

- **#30** Clinician authentication; `actor_id` sourced from the IdP, never user-supplied
- **#31** Clinic isolation (evaluate Postgres row-level security)

> #30 blocks #26 in practice: an audit trail whose `actor_id` the client can set is not
> evidence of anything.

## Phase 8 — Frontend (Next.js)

- **#32** Query interface with language selection
- **#33** Answer view grouped by issuing organisation
- **#34** Conflict view — both passages side by side
- **#35** PDF viewer that jumps to the cited page
- **#36** Staleness banner: "a newer edition of this guideline exists"

## Phase 9 — Human-in-the-loop

- **#37** Clinician feedback on retrieval quality
- **#38** Flag incorrect extractions for review

## Deferred — after the PoC proves itself

Real features, wrong timing. Each is additive to the schema above rather than a
change to it, which is why they can wait.

- Medical speech-to-text (Whisper or a clinical ASR model)
- Multimodal RAG: image embeddings for algorithm diagrams and tables
- FHIR / HL7 integration for EHR/EMR connectivity
- Multi-tenant B2B deployment
