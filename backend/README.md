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
| `app/models/document.py` | `Document` (the work) + `DocumentVersion` (the edition) |
| `app/models/chunk.py` | Retrievable passages, owned by a **version** |
| `app/models/query.py` | Clinician questions — the erasable side of the GDPR line |
| `app/models/audit.py` | Append-only, hash-chained trail |
| `app/schemas/answer.py` | The extractive answer contract |
| `app/services/audit.py` | Chain append, verification, GDPR redaction |
| `alembic/versions/0001_*` | Schema + the triggers that make append-only real |

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

Ingestion, embedding, retrieval, conflict detection, and auth are absent — this is the
schema and the audit substrate only. `AnswerPayload.conflicts` is the contract the
conflict pass will fill: group retrieved chunks by `issuing_org`, and only escalate to
a comparison call when a single result set spans more than one organisation.

Roadmap, deliberately deferred: voice input, multimodal RAG over figures and tables,
FHIR/HL7 integration.

## Embedding dimension

`EMBEDDING_DIM=1536` is baked into `vector(1536)` by migration 0001. Changing it means
re-embedding every chunk. Worth settling before ingestion starts — 18-language support
points toward a multilingual model (e.g. `multilingual-e5-large` at 1024, or Cohere
`embed-multilingual-v3` at 1024) rather than the 1536 default.
