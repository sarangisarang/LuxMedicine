import "server-only";

import { EncryptJWT, jwtDecrypt } from "jose";
import * as client from "openid-client";

import {
  OIDC_CLIENT_ID,
  OIDC_CLIENT_SECRET,
  OIDC_ISSUER,
  SESSION_SECRET,
} from "./config";

// The two cookies the login flow uses, both HTTP-only and encrypted:
//   - the SESSION cookie holds the tokens after a successful login; the browser never sees them.
//   - the TX cookie holds the one-login secrets (PKCE verifier, state, nonce) between the redirect
//     to Keycloak and the callback, and is deleted the moment it is used.
export const SESSION_COOKIE = "lux_session";
export const TX_COOKIE = "lux_oidc_tx";

// How long a session cookie lives before a fresh login is required. Access tokens inside it are
// short-lived and refreshed transparently; this is the outer bound.
export const SESSION_MAX_AGE_SECONDS = 12 * 60 * 60;
export const TX_MAX_AGE_SECONDS = 10 * 60;

export type Session = {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  // ms epoch when the access_token expires — drives the silent refresh in lib/auth.ts.
  expires_at: number;
};

export type LoginTx = {
  state: string;
  nonce: string;
  code_verifier: string;
};

// One discovery per process. openid-client fetches the realm's OpenID configuration and holds the
// client credentials; every route reuses it.
let configPromise: Promise<client.Configuration> | null = null;

export function getOidcConfig(): Promise<client.Configuration> {
  if (!configPromise) {
    configPromise = (async () => {
      // openid-client refuses plain HTTP by default, which is exactly right for production
      // (auth.<domain> is https). In local dev Keycloak is http://localhost:8081, so insecure
      // requests are allowed ONLY when the issuer is http — a prod https issuer never opts in.
      const allowHttp = OIDC_ISSUER.startsWith("http://");
      const config = await client.discovery(
        new URL(OIDC_ISSUER),
        OIDC_CLIENT_ID,
        OIDC_CLIENT_SECRET,
        undefined,
        allowHttp ? { execute: [client.allowInsecureRequests] } : undefined,
      );
      // Also allow it for the later token exchange and refresh calls, not just discovery.
      if (allowHttp) client.allowInsecureRequests(config);
      return config;
    })();
  }
  return configPromise;
}

// A 256-bit key for A256GCM, derived from the passphrase so the config value can be a readable
// secret rather than a raw blob. Recomputed per call (cheap) to avoid caching key material.
async function encryptionKey(): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(SESSION_SECRET),
  );
  return new Uint8Array(digest);
}

export async function seal(
  payload: Record<string, unknown>,
  ttlSeconds: number,
): Promise<string> {
  return new EncryptJWT(payload)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(`${ttlSeconds}s`)
    .encrypt(await encryptionKey());
}

// Null on anything wrong — tampered, expired, wrong key — so a bad cookie is simply "not logged
// in", never a crash.
export async function unseal<T>(token: string | undefined): Promise<T | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtDecrypt(token, await encryptionKey());
    return payload as T;
  } catch {
    return null;
  }
}
