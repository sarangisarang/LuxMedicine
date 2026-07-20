import "server-only";

import { cookies } from "next/headers";
import * as client from "openid-client";

import { APP_BASE_URL } from "./config";
import {
  getOidcConfig,
  readSessionCookie,
  seal,
  sessionCookieOptions,
  SESSION_MAX_AGE_SECONDS,
  unseal,
  writeSessionCookies,
  type Session,
} from "./oidc";

// The per-user access token, from the encrypted session cookie set at login (app/auth/callback).
//
// This replaces the old dev password grant that logged everyone in as dr.smith. The token is a
// real per-user RS256 token carrying that clinician's `sub` and `clinic_id`, so the API's
// verification and row-level security apply per user — the whole point of the OIDC flow. It never
// crosses to the browser: read server-side, passed straight to the backend.

// Thrown when there is no usable session — the route handlers translate it to an auth error, and
// middleware redirects a page load to /auth/login before it gets this far.
export class NotAuthenticated extends Error {}

export async function getAccessToken(): Promise<string> {
  const store = await cookies();
  const session = await unseal<Session>(
    readSessionCookie((name) => store.get(name)?.value),
  );
  if (!session) {
    throw new NotAuthenticated("no session — sign in");
  }

  // Still valid, with a 60s safety margin against clock skew and in-flight requests.
  if (Date.now() < session.expires_at - 60_000) {
    return session.access_token;
  }

  // Expired: refresh silently and re-seal the cookie, so a clinician mid-shift is never bounced to
  // the login page just because 15 minutes passed.
  if (!session.refresh_token) {
    throw new NotAuthenticated("session expired");
  }
  let refreshed;
  try {
    refreshed = await client.refreshTokenGrant(await getOidcConfig(), session.refresh_token);
  } catch {
    // The refresh token is spent or revoked — a real re-login is required.
    throw new NotAuthenticated("refresh failed");
  }

  const next: Session = {
    access_token: refreshed.access_token,
    refresh_token: refreshed.refresh_token ?? session.refresh_token,
    id_token: refreshed.id_token ?? session.id_token,
    expires_at: Date.now() + (refreshed.expires_in ?? 300) * 1000,
  };
  const sealed = await seal({ ...next }, SESSION_MAX_AGE_SECONDS);
  const opts = sessionCookieOptions(APP_BASE_URL.startsWith("https"));
  writeSessionCookies(
    (name, value) => store.set(name, value, opts),
    (name) => store.delete(name),
    sealed,
  );
  return next.access_token;
}
