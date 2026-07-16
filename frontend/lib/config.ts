// Where the backend and the local identity provider live. Server-side only values (the
// Keycloak URL, the dev credentials) never carry the NEXT_PUBLIC_ prefix, so they are not
// bundled into the browser. Only the API base URL, which the browser must know, is public.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// The local Keycloak from backend/docker-compose.yml. Used only by the server-side token
// helper (lib/auth.ts) — see backend/scripts/dev_token.py for the same password grant.
// There is deliberately no auth bypass: the frontend authenticates with a real RS256 token
// against the real issuer, exactly as production will.
export const KEYCLOAK_TOKEN_URL =
  process.env.KEYCLOAK_TOKEN_URL ??
  "http://localhost:8081/realms/luxmedicine/protocol/openid-connect/token";

export const DEV_CLIENT_ID = process.env.DEV_CLIENT_ID ?? "luxmedicine-api";
export const DEV_USERNAME = process.env.DEV_USERNAME ?? "dr.smith";
export const DEV_PASSWORD = process.env.DEV_PASSWORD ?? "dev-password";
