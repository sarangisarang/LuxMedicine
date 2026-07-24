# Deploy runbook — ka→en query translation + corpus upload (2026-07-24)

Two changes are built, tested locally, and NOT yet on prod:
- **ka→en query translation** (retrieval fix): `pipeline.answer_query` translates a non-corpus-language
  query before retrieval. Backend only.
- **corpus upload feature** (folder upload → split → ingest, with the licence gate): backend +
  frontend + **migration 0020** (`document_versions.license_status` / `provenance_source`).

Both change the retrieval path / corpus, so this is deployed AFTER the graded re-baseline (done:
`eval/runs/20260724T181426Z-gemini-2.5-flash.json`).

> ⚠️ **This is a co-tenant stack.** The box also runs ATOB's live prod. The 2026-07-23 12-minute outage
> came from a compose mistake here. Read every step; do not improvise. Never touch `deploy-nginx-1` with
> anything but `nginx -t` / `nginx -s reload`. Use `docker-compose.cohost.yml`, never `prod.yml`.

Server: `root@178.105.170.162`, app at `/opt/luxmedicine`, containers `luxmedicine-prod-*`.

---

## 0. Pre-flight (safe, do first)

```bash
# On the server. Confirm the running stack matches the file BEFORE changing anything.
cd /opt/luxmedicine
deploy/check-drift.sh                       # must say "no drift"
# Take a fresh DB backup (migration 0020 runs on api startup).
deploy/backup.sh
# Record ATOB is healthy NOW, so you can tell if YOU broke it later.
curl -s -o /dev/null -w '%{http_code}\n' https://api.atobtransport.de/api/transport-types   # 401/403 = healthy
```

## 1. Get the new code onto the server (/opt/luxmedicine is NOT git)

Pick ONE. The code lives in the local repo; the server needs the updated `backend/` and `frontend/`
source (compose builds `./backend` and `./frontend`).

**Option A — via GitHub (needs the repo push; confirm PUBLIC/PRIVATE is acceptable first):**
```bash
# Local (Windows Git Bash), from the repo root:
git add -A && git commit -m "ka->en query translation + corpus upload feature (licence-gated)"
git push origin main
# Server: clone fresh into a temp dir and rsync the SOURCE into place (never over storage/.env/models/gcp).
ssh root@178.105.170.162 '
  set -e
  rm -rf /tmp/lux-src && git clone --depth 1 https://github.com/sarangisarang/LuxMedicine.git /tmp/lux-src
  rsync -a --delete /tmp/lux-src/backend/  /opt/luxmedicine/backend/  \
    --exclude=.venv --exclude=__pycache__ --exclude="*.pyc" --exclude=storage
  rsync -a --delete /tmp/lux-src/frontend/ /opt/luxmedicine/frontend/ \
    --exclude=node_modules --exclude=.next
'
```

**Option B — rsync straight from local (no push; needs rsync in Git Bash):**
```bash
# Local (Windows Git Bash), from the repo root:
rsync -az --delete backend/  root@178.105.170.162:/opt/luxmedicine/backend/  \
  --exclude=.venv --exclude=__pycache__ --exclude="*.pyc" --exclude=storage
rsync -az --delete frontend/ root@178.105.170.162:/opt/luxmedicine/frontend/ \
  --exclude=node_modules --exclude=.next
```

> Do NOT `--delete` the server's `backend/storage`, `deploy/gcp`, `deploy/.env.prod`, or `models/` — the
> excludes above protect them, but double-check nothing under those paths is touched.

## 2. Build and recreate (api runs migration 0020 on startup)

```bash
# Server, /opt/luxmedicine. ALWAYS --env-file + cohost.yml.
docker compose --env-file deploy/.env.prod -f docker-compose.cohost.yml build api web
docker compose --env-file deploy/.env.prod -f docker-compose.cohost.yml up -d api web
# api's entrypoint runs `alembic upgrade head` → 0020 applies. Confirm the new column exists:
docker compose --env-file deploy/.env.prod -f docker-compose.cohost.yml exec -T db \
  psql -U luxmed -d luxmedicine -c "\d document_versions" | grep -i license_status
# And that the migration head is 0020:
docker compose --env-file deploy/.env.prod -f docker-compose.cohost.yml exec -T api alembic current
```

## 3. nginx reload — MANDATORY after rebuilding `web` (skipping it = 502 outage)

`web` got a new container IP; ATOB's nginx cached the old one. Reload (do NOT restart/recreate nginx):
```bash
docker exec deploy-nginx-1 nginx -t          # config still valid
docker exec deploy-nginx-1 nginx -s reload    # graceful
```
(`api` is not proxied by name, so it needs no reload — but `web` always does.)

## 4. Verify — ours AND ATOB

```bash
cd /opt/luxmedicine
deploy/check-drift.sh                                                        # no drift, all 5 match
curl -s -o /dev/null -w '%{http_code}\n' https://api.atobtransport.de/api/transport-types  # still 401/403
```
Then in a browser at `https://luxmedicin.atobtransport.de`:
- Ask a **Georgian** medical question → it should now retrieve and answer (the ka→en fix).
- Open the collapsed **"Add guidelines (admin)"** section → the licence select + source + progress bar are there.
- (Optional) upload one small public-domain PDF with licence = Public domain → watch the 0-100% bar → it
  goes ACTIVE. Try licence = Unknown → it ingests but stays quarantined (not searchable).

## 5. Rollback (if anything is wrong)

- **App broken, ATOB fine:** revert the source and rebuild —
  `git -C /tmp/lux-src checkout <previous-sha>` (or rsync the previous source back) → repeat step 2–3.
- **Migration to undo:** `docker compose ... exec -T api alembic downgrade -1` (drops the two columns; safe,
  they are additive).
- **ATOB down (502/504/000):** you touched the shared stack — restore nginx first
  (`docker exec deploy-nginx-1 nginx -s reload`), and if that fails, re-check you used cohost.yml and did
  not recreate `deploy-nginx-1`. See deploy/README.md.
- **DB corrupted:** restore the pre-flight backup (deploy/backup.sh's output).

## Not in this deploy (separate, small)
- The demo banner still says "inference is not EU-hosted" — now false. Fix its wording narrowly
  ("inference in the EU (europe-west3)"), keep "do not enter real patient data". Needs a `web` change +
  this same reload. See [[vertex-eu-switch]].
