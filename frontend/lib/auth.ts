import "server-only";

import {
  DEV_CLIENT_ID,
  DEV_PASSWORD,
  DEV_USERNAME,
  KEYCLOAK_TOKEN_URL,
} from "./config";

// The local-dev token bridge — the browser equivalent of backend/scripts/dev_token.py.
//
// `import "server-only"` makes this module a build error if it is ever imported into a
// client component, so the dev credentials and the raw token can never be bundled into the
// browser. The token is fetched here, on the server, and only the answer crosses to the
// client.
//
// This is NOT an auth bypass: it obtains a genuine RS256 token from the real issuer via the
// password grant the dev realm enables (backend/keycloak/realm-luxmedicine.json). The token
// carries a real `sub` and `clinic_id`, so the frontend exercises the same verification and
// row-level-security path production will. Swapping this for a proper OIDC login flow is a
// change to this one file.

let cached: { token: string; expiresAt: number } | null = null;

export async function getAccessToken(): Promise<string> {
  // Tokens live 900s (realm accessTokenLifespan); reuse within a 60s safety margin.
  if (cached && Date.now() < cached.expiresAt - 60_000) {
    return cached.token;
  }

  const response = await fetch(KEYCLOAK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "password",
      client_id: DEV_CLIENT_ID,
      username: DEV_USERNAME,
      password: DEV_PASSWORD,
    }),
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(
      `could not get a dev token from Keycloak (${response.status}). ` +
        "Is `docker compose up keycloak` running in backend/?",
    );
  }

  const data = (await response.json()) as {
    access_token: string;
    expires_in: number;
  };
  cached = {
    token: data.access_token,
    expiresAt: Date.now() + data.expires_in * 1000,
  };
  return cached.token;
}
