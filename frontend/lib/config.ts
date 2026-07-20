// Where the backend and the local identity provider live. Server-side only values (the Keycloak
// URL, the client secret) never carry the NEXT_PUBLIC_ prefix, so they are not bundled into the
// browser. Only the API base URL, which the browser must know, is public.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// --- Real per-user login (OIDC Authorization Code, BFF pattern) ---
//
// All server-side: the Next server is a confidential client, so the secret and the tokens never
// reach the browser (see lib/oidc.ts). OIDC_ISSUER is Keycloak's realm URL as BOTH the browser
// (for the login redirect) and this server (for discovery + token exchange) reach it — in dev
// that is localhost:8081 for both when running `npm run dev`; in prod it is auth.<domain>, fixed
// by KC_HOSTNAME so the two agree.
export const OIDC_ISSUER =
  process.env.OIDC_ISSUER ?? "http://localhost:8081/realms/luxmedicine";
export const OIDC_CLIENT_ID = process.env.OIDC_CLIENT_ID ?? "luxmedicine-web";
export const OIDC_CLIENT_SECRET = process.env.OIDC_CLIENT_SECRET ?? "dev-web-secret";

// Where the browser reaches this app — the base for the redirect and post-logout URIs, which must
// exactly match what the luxmedicine-web client allows in Keycloak.
export const APP_BASE_URL = process.env.APP_BASE_URL ?? "http://localhost:3000";

// The passphrase the session cookie is encrypted under (JWE). A dev default here, a real random
// value in production via the environment — see deploy/.env.prod.
export const SESSION_SECRET =
  process.env.SESSION_SECRET ?? "dev-session-secret-change-me-in-production";
