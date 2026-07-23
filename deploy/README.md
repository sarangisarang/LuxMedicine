# Deploying LuxMedicine

A single-server deployment: nginx terminates TLS and reverse-proxies three subdomains to the
frontend, the API, and Keycloak; everything else stays on the internal Docker network. Files here:

- `../docker-compose.prod.yml` — the production stack.
- `nginx/templates/luxmedicine.conf.template` — the reverse proxy + TLS config.
- `.env.prod.example` — copy to `.env.prod` (git-ignored) and fill in.

## Co-hosted deploys: the live stack is `docker-compose.cohost.yml`, NOT `docker-compose.prod.yml`

**Using the wrong file is a full outage, and it looks like three unrelated bugs.** Both files
declare the same project (`luxmedicine-prod`) and the same service names, so
`docker compose -f docker-compose.prod.yml up -d <service>` is accepted without complaint and
silently *replaces* a co-tenant container with a standalone one. Three things vanish at once:

- the `deploy_internal` attachment and the `lux-web` / `lux-keycloak` aliases ATOB's nginx
  proxies to → `nginx -t` fails with "host not found in upstream", every page 502;
- `GEMINI_API_KEY` (prod.yml passes only `GEMINI_MODEL`) → the api exits 0 on startup with
  "No API key was provided" and restart-loops, which reads as a crash, not a config gap;
- `ALLOW_NON_EU_INFERENCE`, the demo waiver this deployment runs under.

Seen exactly that way (2026-07-23): a frontend rebuild issued against `prod.yml` took the site
down for ~12 minutes and cost 39 api restarts before the cause was found. Always:

    docker compose --env-file deploy/.env.prod -f docker-compose.cohost.yml up -d [service]

`prod.yml` is the standalone reference deployment (own proxy, own certbot, publishes 80/443).
It is not wrong — it is for a different server. ATOB stayed 401-healthy throughout; the
co-tenancy rules held. Related: `prod.yml`'s keycloak also still carried `--optimized`, which
makes the stock image ignore `KC_DB: postgres` and exit 2 — dropped in that commit, but the
container it broke had been running for three days, so nothing surfaced it until a restart.

## Co-hosted deploys: reload nginx after rebuilding `web` or `keycloak`

**This is an outage if it is skipped, and it looks like a code failure.** On the co-hosted
stack, ATOB's nginx proxies to the Docker network aliases `lux-web` and `lux-keycloak`. nginx
resolves an upstream hostname **once, at startup**, and caches the address. Rebuilding a
container gives it a new IP, so nginx keeps connecting to the old one and every page returns
502 while the container itself is healthy and serving.

Seen exactly that way (2026-07-23): `docker compose up -d web` moved the frontend from
172.18.0.6 to .0.7, DNS resolved correctly, `wget http://lux-web:3000/` from inside nginx
worked — and the site was 502 because nginx was still dialling 172.18.0.6.

    docker exec deploy-nginx-1 nginx -t          # config still valid
    docker exec deploy-nginx-1 nginx -s reload   # graceful: workers finish in flight

## After every `up -d`: confirm the running stack matches the file

    deploy/check-drift.sh    # exit 0 = every container matches the compose file; exit 1 = drift

A container keeps running the config it was *created* from, not the file on disk, so a fixed
file and a stale container can disagree indefinitely with nothing to show for it — Keycloak's
`--optimized` sat that way for three days until a restart turned the gap into a 502. This
compares each container's `com.docker.compose.config-hash` label against the hash recomputed
from the file and exits 1 on any mismatch, so "I edited the file" and "the fix is actually
running" stop being the same claim. Run it after the reload above; a drift means recreate that
service, not just reload nginx.

`reload` is not `restart` and is emphatically not `up`/`recreate` — ATOB's nginx must not be
recreated (its container IP is pinned in this stack's `extra_hosts`, and it serves a live
business). Rebuilding `api` does not need this: nothing proxies to it by name.

## One prerequisite that is CODE, not infrastructure — read first

This infra is ready, but the product is not fully deployable until the code gap below closes. It is
not this directory's job to fix; it is flagged so the deployment is not stood up on a false floor.

Resolved since this note was first written: **the frontend now has real per-user login.**
`frontend/lib/auth.ts` reads a per-user session established by the OIDC Authorization-Code flow
(`frontend/app/auth/{login,callback,logout}`, BFF pattern — tokens live in an encrypted HTTP-only
cookie, never in the browser). The old dev password grant that logged everyone in as dr.smith is
gone. The compose `web` service threads through `OIDC_ISSUER` (= `https://auth.<domain>/...`),
`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `SESSION_SECRET`, and `APP_BASE_URL`; the confidential
`luxmedicine-web` client is in the realm import with strict redirect URIs. Verified end to end
against a live Keycloak (login → token → per-user `clinic_id` claim).

1. **The extractor still calls the Gemini API, not Vertex.** `ENVIRONMENT=production` makes the API
   refuse a non-EU inference endpoint, so it will not even boot against the public Gemini API. The
   EU-residency path is Vertex AI in an EU region (`europe-west3`), which needs the extractor
   switched to the Vertex client and Google credentials mounted. The env vars are already threaded
   through the compose file (`GOOGLE_CLOUD_*`); the client swap is the remaining work.

## First-time deploy

### 1. DNS
Point A/AAAA records at the server for all three names:

    <domain>            e.g. luxmedicin.de
    api.<domain>
    auth.<domain>

### 2. Secrets
    cp deploy/.env.prod.example deploy/.env.prod
    # fill every blank; generate long random values for the passwords, the client secrets
    # (KEYCLOAK_ADMIN_CLIENT_SECRET, OIDC_CLIENT_SECRET), and SESSION_SECRET. The two *_CLIENT_SECRET
    # values must also be set on their clients in Keycloak after the realm import (see each note).

### 3. TLS certificate (before nginx serves 443)
nginx will not start with the 443 servers until the certificate exists, so issue it first with a
one-off standalone certbot (port 80 must be free):

    docker run --rm -p 80:80 \
      -v luxmedicine-prod_certbot-certs:/etc/letsencrypt \
      -v luxmedicine-prod_certbot-webroot:/var/www/certbot \
      certbot/certbot certonly --standalone --agree-tos --no-eff-email \
      --email you@example.com \
      -d luxmedicin.de -d api.luxmedicin.de -d auth.luxmedicin.de

One SAN certificate covers all three; its files land under the base domain's directory, which is
what the nginx template points at. (Renewals afterwards run automatically via the `certbot` service
using the webroot challenge — no downtime.)

### 4. Bring up the stack
    docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml up -d --build

On first boot: Keycloak imports the realm into its Postgres, and the API runs `alembic upgrade
head` (creating the schema through migration 0017). The corpus PDFs must be placed in the
`storage` volume and match `document_versions.storage_uri`, exactly as in dev.

### 5. Rotate the admin client secret
The committed realm ships the luxmedicine-admin client with the local secret `dev-admin-secret`.
After the first import, in the Keycloak admin console (`https://auth.<domain>` → Clients →
luxmedicine-admin → Credentials) set a real secret, put the SAME value in
`KEYCLOAK_ADMIN_CLIENT_SECRET` in `.env.prod`, and restart the api:

    docker compose --env-file deploy/.env.prod -f docker-compose.prod.yml up -d api

## Verifying

- `https://<domain>` serves the app; `https://auth.<domain>` serves the Keycloak login.
- A token minted at `auth.<domain>` carries `iss=https://auth.<domain>/realms/luxmedicine`, which
  is exactly the API's `OIDC_ISSUER` — a mismatch here is the #1 cause of every request 401'ing.
- Registration end to end: a clinic-admin mints an invite, a newcomer redeems it at `/register`,
  then signs in through `https://<domain>` → `auth.<domain>` and lands with their own `clinic_id`
  in the token. Signing out (`/auth/logout`) ends the Keycloak session too, not just the cookie.

## Notes

- Only the proxy publishes ports (80/443). The database, Keycloak, and the API are unreachable from
  the internet — the API is reached by the frontend over the internal network, and exposed at
  `api.<domain>` only for tooling; drop that server block if you do not want it public.
- Keycloak runs in production mode against its own Postgres, so realm and users persist across
  restarts (unlike dev's ephemeral H2). Edit the realm in the console after import, not by editing
  the JSON — a re-import will not overwrite an existing realm.
