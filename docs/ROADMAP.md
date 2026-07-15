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
- **#16** Brand-name query expansion — "Renitec" must reach enalapril
- **#17** Staleness detection: flag hits whose version has a `superseded_by`

> #16 was scoped from a guess and re-scoped from a measurement. The guess: cross-lingual
> jargon and lay phrasing would need a synonym layer. Measured against
> multilingual-e5-large, they do not — every one of these reaches an English hypertension
> passage unaided:
>
> ```
> "ჰიპერტენზიის მკურნალობა"   margin 0.0738
> "მაღალი წნევის მკურნალობა"   margin 0.0804
> "Hypertonie Behandlung"      margin 0.0740
> "лечение высокого давления"  margin 0.1160
> "ACEi first line"            margin 0.0803
> ```
>
> A medical thesaurus would have been months spent on a problem that does not exist.
>
> What the model genuinely cannot do is brand names, and it fails *below chance*:
>
> ```
> "Renitec dose"  -> enalapril 0.8254 | metformin 0.8264   margin -0.0010
> "Vasotec dose"  -> enalapril 0.8171 | metformin 0.8193   margin -0.0022
> ```
>
> Renitec is enalapril. A clinician asking by the name on the box gets a diabetes drug,
> ranked first. No embedding reaches that association — it is a fact the model was never
> shown, not a nuance it fumbles — so it is a curated table with provenance on every row.
> Wrong aliases answer confidently about the wrong drug.

> #15 was justified by a claim that turned out to be false, and is still worth building.
>
> The claim: pure vector search misses exact drug names because the embedding drifts
> toward a semantic neighbour. Measured against multilingual-e5-large, with five ACE
> inhibitors whose passages differ only by drug name and dose: **top-1 correct, 5 out of
> 5.** No drift.
>
> What the numbers show instead is worse, because it is quieter:
>
> ```
> enalapril query -> 0.9126 enalapril
>                    0.8736 lisinopril    <- a different drug
>                    0.8693 perindopril
>                    0.8666 captopril
>                    0.8585 ramipril
> ```
>
> All five inside 0.055. Top-1 is right by a margin thinner than noise, and any top-k
> above 1 returns four passages about drugs nobody asked about — each looking exactly as
> relevant as the right one. #18 is what gets handed that list. Lexical search holds no
> opinion about near-misses: the token is present or it is not.

## Phase 4 — Extractive answering

- **#18** Strict extraction prompt returning `AnswerPayload` as structured output
- **#19** **Verbatim citation validation** — reject any `quote` not found character-for-character in its cited chunk
- **#20** `no_answer_reason` path when the corpus cannot answer
- **#21** ~~Per-statement guarantee: no citations ⇒ the statement never ships~~ — dissolved by #19

> #19 is the strongest anti-hallucination mechanism available here, and it is a cheap
> substring check rather than a model call. Because the schema is extractive, a
> fabricated quote is *mechanically detectable*: it will not appear in the source text.
> Free-form generation forfeits this — which is one more reason the MDR positioning
> pays for itself technically, not just legally.
>
> Building it exposed a hole in the schema it was meant to protect. #19 validates
> `quote`. It never validated `ExtractedStatement.text` — a model-written claim with the
> citation attached as evidence beneath it. So this passed every check:
>
> ```
> text  = "Enalapril is contraindicated in renal impairment"    <- invented
> quote = "The target dose of enalapril is 20 mg twice daily"    <- real, verbatim
> ```
>
> Fabricated advice wearing a genuine citation, which is worse than a bare hallucination
> because the citation is what lends it credibility — and the claim is the headline a
> clinician reads, with the quote as small print. Statements are gone: a source group
> carries citations, a citation carries a quote, and there is no field left to write a
> claim into.
>
> That also settles **#21** structurally. "A statement with no citation" is now
> unrepresentable rather than forbidden, so there is no rule left to enforce.
>
> `ConflictFinding.description` is the same category of prose and survives for now —
> settle it at #24, before a prompt is written that fills it.

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
