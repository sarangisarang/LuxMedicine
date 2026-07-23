# Switching inference to Vertex AI in the EU

Everything on the code side is built. This is the fill-in-the-blanks procedure, in the order
that keeps each step's result readable.

## What changes, and what does not

`GOOGLE_CLOUD_PROJECT` alone selects the path. Set, the extractor constructs a Vertex client
pinned to `GOOGLE_CLOUD_LOCATION`; unset, it uses the free developer API on the global
endpoint. No code edit, no image rebuild, no compose edit — the variables and the credentials
mount are already threaded through.

An API key is **not** accepted on the Vertex path. The SDK raises instead of quietly falling
back to global, which is the behaviour that makes the residency promise checkable rather than
aspirational.

## Order of operations

The sequence matters, for the reason #47 records: change one thing at a time or the
measurement cannot attribute the difference.

### 1. Baseline first, on today's question set

```bash
python -m app.cli.evaluate                 # writes eval/runs/<timestamp>-<model>.json
```

Run this **before** switching, on the current model, without touching `eval/questions.yaml`.
Expanding the question set and changing the provider in the same step makes the two
indistinguishable.

### 2. On the Google side

- enable billing on the project
- enable the Vertex AI API
- create a service account with **Vertex AI User** (`roles/aiplatform.user`) — nothing more
- download a JSON key

### 3. Put the key where the container can read it

```bash
scp vertex-sa.json root@<host>:/opt/luxmedicine/deploy/gcp/
ssh root@<host> "chmod 600 /opt/luxmedicine/deploy/gcp/vertex-sa.json"
```

`deploy/gcp/` is mounted read-only at `/run/gcp` and is mounted whether or not it holds
anything, so this step needs no compose change. Nothing in that directory is committed.

### 4. Fill in `deploy/.env.prod`

```ini
GOOGLE_CLOUD_PROJECT=<project-id>
GOOGLE_CLOUD_LOCATION=europe-west3
GOOGLE_APPLICATION_CREDENTIALS=/run/gcp/vertex-sa.json
GEMINI_API_KEY=
ALLOW_NON_EU_INFERENCE=false
```

`GEMINI_API_KEY` is cleared deliberately: leaving a key set on the Vertex path is what the
SDK refuses, and finding out from a raise is better than finding out from a bill.

### 5. Restart and **verify**, do not assume

```bash
cd /opt/luxmedicine
docker compose --env-file deploy/.env.prod -f docker-compose.cohost.yml up -d api
docker exec luxmedicine-prod-api-1 python -m app.cli.check_inference
```

This is the load-bearing step. It prints the endpoint the SDK **actually resolved**, not the
region that was requested:

```
  resolved endpoint    https://europe-west3-aiplatform.googleapis.com/
  keeps processing EU  True
  residency waived     False

  OK — inference resolves to a European Vertex endpoint.
```

Exit 0 only in that state. A config saying `europe-west3` while the client talks to the
global endpoint is a documented failure (google-genai #27984, where the JS SDK drops the
location when an API key is present) — so "I set the region" and "it runs in the region" are
checked as two separate claims. Makes no model call; costs no quota.

### 6. Re-run the SAME question set

```bash
python -m app.cli.evaluate
```

Same file, same questions, so the diff against step 1 is attributable to the provider and
nothing else. Compare per question, never in aggregate — four prompt wordings once produced
an identical aggregate while three individual cases swapped underneath it
(`services/extractor_claude.py` records that measurement).

Only after this is the question set worth expanding.

## Rolling back

Clear `GOOGLE_CLOUD_PROJECT`, restore `GEMINI_API_KEY`, set `ALLOW_NON_EU_INFERENCE=true`,
restart. The corpus, the audit chain and the users are untouched by any of this — inference
is the last link, not the store.

## What the endpoint check does NOT establish

`check_inference` proves the technical route: which host the SDK resolved, and therefore where
the computation physically happens. That is necessary and it is not the whole promise.

"The data is not used for training" and "it stays in the EU as a matter of obligation" are
**contractual**, not technical — Google's data-processing terms and the DPA for the project.
Vertex's terms differ from the free developer API's on exactly this point, which is one of the
reasons to switch, but a resolved hostname is not evidence of a signed agreement.

If any of this is ever stated to a clinician, record the contractual side separately: which
terms apply, accepted when, and by which account. The endpoint check answers "where does it
run"; it cannot answer "what may they do with it".

## What this does and does not fix

**Does:** data residency, the daily-quota ceiling that keeps the eval small, and the training
question. All three are real and all three block growth.

**Does not, as far as anyone has measured:** the intermittent `verification_failed`. That was
attributed to model unfaithfulness and the attribution has not held up — non-determinism was
ruled out at temperature 0 (0/6 flips), and glyph damage was ruled out for the observed case
(0 of the 12 retrieved passages carried a corruption marker; NHLBI holds no `(cid:N)` at all).
The cause is open. `services/quote_diagnosis.py` classifies a rejection and is the tool for
it; the missing input is the rejected quotes themselves. Do not expect Vertex to close it,
and do not let a coincidental improvement after the switch be read as having closed it.
