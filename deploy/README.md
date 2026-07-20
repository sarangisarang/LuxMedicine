# Deploying LuxMedicine

A single-server deployment: nginx terminates TLS and reverse-proxies three subdomains to the
frontend, the API, and Keycloak; everything else stays on the internal Docker network. Files here:

- `../docker-compose.prod.yml` — the production stack.
- `nginx/templates/luxmedicine.conf.template` — the reverse proxy + TLS config.
- `.env.prod.example` — copy to `.env.prod` (git-ignored) and fill in.

## Two prerequisites that are CODE, not infrastructure — read first

This infra is ready, but the product is not fully deployable until two code gaps close. Neither is
this directory's job to fix; both are flagged so the deployment is not stood up on a false floor.

1. **The frontend has no per-user login.** `frontend/lib/auth.ts` obtains ONE token with the dev
   password grant (dr.smith), for every request. Deployed as-is, every visitor acts as dr.smith in
   dr.smith's clinic — the #51 registration would create real users who then cannot sign in as
   themselves. The frontend needs a real OIDC Authorization-Code login (redirect to
   `auth.<domain>`, callback, per-user session). Its own docstring says this is "a change to this
   one file"; the reverse proxy here already exposes Keycloak for exactly that redirect, so no
   infra change is needed when it lands.

2. **The extractor still calls the Gemini API, not Vertex.** `ENVIRONMENT=production` makes the API
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
    # fill every blank; generate long random values for the passwords and the client secret.

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
- Registration end to end (once the frontend login lands): a clinic-admin mints an invite, a
  newcomer redeems it at `/register`, and logs in with their own `clinic_id` in the token.

## Notes

- Only the proxy publishes ports (80/443). The database, Keycloak, and the API are unreachable from
  the internet — the API is reached by the frontend over the internal network, and exposed at
  `api.<domain>` only for tooling; drop that server block if you do not want it public.
- Keycloak runs in production mode against its own Postgres, so realm and users persist across
  restarts (unlike dev's ephemeral H2). Edit the realm in the console after import, not by editing
  the JSON — a re-import will not overwrite an existing realm.
