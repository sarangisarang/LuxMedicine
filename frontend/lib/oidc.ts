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

// The sealed session holds three JWTs (access + refresh + id) and runs ~4.8 KB — over the browser's
// hard ~4 KB per-cookie limit, which browsers enforce by SILENTLY DROPPING the cookie. The login
// then loops: the callback sets a cookie the browser discards, every page sees no session, and it
// bounces back to /auth/login. So the session is split across numbered cookies (lux_session.0,
// lux_session.1, …), each safely under the limit, and reassembled on read. (httpx has no such
// limit, which is why the flow passed every scripted test and only failed in a real browser.)
const CHUNK_SIZE = 3500;
const MAX_CHUNKS = 8;

export const SESSION_COOKIE_FIRST = `${SESSION_COOKIE}.0`;

export function sessionCookieOptions(secure: boolean) {
  return {
    httpOnly: true,
    secure,
    sameSite: "lax" as const,
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
  };
}

// Split `sealed` into lux_session.0..N and clear any higher-index chunks a previous, larger session
// left behind (so shrinking the session never leaves a stale tail that corrupts the next read).
export function writeSessionCookies(
  set: (name: string, value: string) => void,
  del: (name: string) => void,
  sealed: string,
): void {
  const chunks: string[] = [];
  for (let i = 0; i < sealed.length; i += CHUNK_SIZE) {
    chunks.push(sealed.slice(i, i + CHUNK_SIZE));
  }
  chunks.forEach((chunk, i) => set(`${SESSION_COOKIE}.${i}`, chunk));
  for (let i = chunks.length; i < MAX_CHUNKS; i++) del(`${SESSION_COOKIE}.${i}`);
}

// Reassemble the sealed session from its chunks (in order, stopping at the first gap).
export function readSessionCookie(
  get: (name: string) => string | undefined,
): string | undefined {
  const parts: string[] = [];
  for (let i = 0; i < MAX_CHUNKS; i++) {
    const value = get(`${SESSION_COOKIE}.${i}`);
    if (value === undefined) break;
    parts.push(value);
  }
  return parts.length ? parts.join("") : undefined;
}

export function clearSessionCookies(del: (name: string) => void): void {
  for (let i = 0; i < MAX_CHUNKS; i++) del(`${SESSION_COOKIE}.${i}`);
}
