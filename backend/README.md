# LuxMedicine — Backend

Clinical Search & Retrieval Engine. Returns source-attributed extracts from clinical
guidelines. **It does not diagnose, recommend, or advise** — see *Positioning* below.

## Setup

```bash
git config core.hooksPath .githooks   # once per clone — blocks committing secrets
cp .env.example .env                  # real values go here; .env is gitignored
docker compose up -d                  # Postgres 17 + pgvector on :5433
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

`alembic upgrade head --sql` prints the DDL without touching a database — useful for
review, and it is how the schema was verified before the container existed.

## Positioning (read before adding features)

The regulatory line we stay on is not a disclaimer, it is `app/schemas/answer.py`.
That schema has no field for a recommendation, an assessment, or a suggested action.
The only clinical text that can leave the system is a `quote` — a verbatim span —
attached to a `Citation` naming the edition and page it came from.

If a feature request cannot be expressed in that schema without adding *what the
system advises*, the answer is not to widen the schema. It is to re-open the MDR
question first, because that field is what turns this into a Class IIa device.

## Layout

| Path | Role |
|---|---|
| `app/schemas/answer.py` | The extractive answer contract — where the MDR line lives |
| `app/services/pipeline.py` | The query flow: retrieve → extract → validate → record |
| `app/services/validation.py` | #19 — verbatim citation checking |
| `app/services/answering.py` | Assembly: the model picks a passage and a span, nothing else |
| `app/services/audit.py` | Chain append, verification, GDPR redaction |
| `app/services/audit_export.py` | Reconstructing the trail for a dispute |
| `app/services/retrieval.py` | Vector + lexical search, fused by rank |
| `app/services/extraction.py` | PDF text with a page ledger |
| `app/services/chunking.py` | Section-aware chunking; typography tells a heading from a dose |
| `app/models/` | documents / versions / chunks / queries / audit_log / drug_aliases |
| `alembic/versions/` | 0001–0007; the triggers that make append-only real are in 0001 |

## Three decisions worth knowing

**Chunks belong to versions, not documents.** This one join is what makes both PoC
features possible. An audit row records chunk ids; each chunk resolves to an exact
edition and page. Guidelines get superseded, never deleted (`status = archived`,
`superseded_by` set), so an answer from 2024 stays reproducible after the 2026
edition lands — and the staleness warning has something to point at.

**Erasure is redaction, not deletion.** A question like *"45yo male, reduced EF…"* is
pseudo-personal data and must be erasable. But deleting the `queries` row would force
an UPDATE or DELETE on `audit_log` to clear the reference — which the append-only
trigger rejects, as it should. So `redact_query()` nulls `text` and stamps
`redacted_at`, leaving the row and its `text_hash`. Both guarantees hold at once: the
content is gone, and the chain still proves which question was asked, by whom, against
which sources.

**Append-only is enforced in the database.** A `BEFORE UPDATE OR DELETE` trigger, a
separate `BEFORE TRUNCATE` trigger (truncate bypasses row-level triggers), and
`REVOKE` on the app role when `APP_DB_ROLE` is set. An ORM-level guard would be
advisory only. A superuser can still drop the trigger — which is why the hash chain
sits on top: `row_hash = sha256(prev_hash || canonical(payload))`, so tampering stays
*detectable* even by someone who can bypass every access control.

Appends take `pg_advisory_xact_lock` before reading the chain tail. Without it, two
concurrent requests read the same tail and write rows claiming the same `prev_hash` —
a forked chain that verifies as tampered.

`GET /audit/verify` walks the chain end to end. Schedule it. A chain nobody checks
proves nothing.

## Tests

```bash
./.venv/Scripts/pytest.exe -q
```

**Bare `pytest`, not `python -m pytest`.** They are not interchangeable: the module form
puts the working directory on `sys.path` and the bare form does not, so a broken import
passes under one and fails under the other. CI runs the bare form. Running the module
form locally once hid an `ImportError` from every local run until CI caught it.

`tests/test_audit_chain.py` runs without a database and pins the chain's sensitivity:
every hashed field, plus chunk-id *order*, must move `row_hash`.

## Not yet built

**No HTTP endpoint for asking a question.** The pipeline is complete and tested;
`answer_query()` takes `actor_id` as a parameter, and only a verified identity may supply
one (#30). An endpoint now would take the actor from the request — and a trail whose
author field the client sets is not evidence, it is a log again. The endpoint is twenty
lines once identity exists.

**The extractor has never been run.** `app/services/extractor_claude.py` needs an API key
and spends money per call. Everything else here was measured before it was trusted; that
file is the exception and says so. Run it before trusting a clinician's question to it —
`tests/test_embedding_real.py` is the precedent.

**`drug_aliases` is empty.** The mechanism works and is tested; the data does not exist,
and it cannot be invented — brand names are assigned per market, they change, and a wrong
one answers confidently about the wrong drug. Load from a registry:

```bash
python -m app.cli.load_aliases registry.csv --source "…, 2026-06" --dry-run
```

The loader refuses to resolve a conflict: a brand that means two different drugs is a
question for a pharmacist, not a merge strategy. It exits non-zero and writes nothing for
those rows.

**One brand means one drug, globally.** `UNIQUE(alias)` cannot express "this brand is
enalapril in one country and something else in another" — a real phenomenon, and the
schema has no room for it. Fine while a deployment serves one market; it breaks at #31,
where whichever mapping loaded first would silently win for every clinic.

**Conflict detection (#24, #25) and a frontend (#32–#36) are absent.** Deliberately
deferred: scanned-PDF OCR (#13), voice input, multimodal RAG, FHIR/HL7.

## Embedding dimension

`EMBEDDING_DIM=1024` is pinned as a literal in migration 0001 — `multilingual-e5-large`,
self-hosted so clinical text is embedded inside the EU. Changing it means re-embedding
every chunk **and** a new migration; `tests/test_embedding_dim.py` pins the literal
against `Settings` and the `Chunk` column, because the drift would otherwise surface as a
runtime dimension error rather than a review-time diff.

**That EU argument is currently half true.** The embedding runs locally. Extraction (#18)
calls a hosted model, so the clinician's question — which is pseudo-personal data, which
is why `queries` is erasable at all — leaves. `inference_geo` is the lever (a first-party
request parameter, absent on Bedrock and Vertex), and its accepted values are not
established here, so it is exposed as an unset option rather than guessed at. Settle it
before any real deployment.
