import { NextRequest, NextResponse } from "next/server";
import * as client from "openid-client";

import { APP_BASE_URL } from "@/lib/config";
import {
  getOidcConfig,
  seal,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
  TX_COOKIE,
  unseal,
  type LoginTx,
  type Session,
} from "@/lib/oidc";

// Keycloak sends the browser back here with a code. Validate it against the secrets from the login
// step, exchange it for tokens (server-side, with the client secret and the PKCE verifier), and
// store the tokens in the session cookie — the browser gets only the encrypted cookie, never a
// token.
export async function GET(request: NextRequest) {
  const config = await getOidcConfig();

  const tx = await unseal<LoginTx>(request.cookies.get(TX_COOKIE)?.value);
  if (!tx) {
    // No transaction — a stale or forged callback. Start over rather than trust it.
    return NextResponse.redirect(`${APP_BASE_URL}/auth/login`);
  }

  // Reconstruct the callback URL from the PUBLIC base, not request.url, so it matches the
  // registered redirect_uri even when this runs behind the reverse proxy on an internal host.
  const currentUrl = new URL(`${APP_BASE_URL}/auth/callback${new URL(request.url).search}`);

  let tokens;
  try {
    tokens = await client.authorizationCodeGrant(config, currentUrl, {
      pkceCodeVerifier: tx.code_verifier,
      expectedState: tx.state,
      expectedNonce: tx.nonce,
    });
  } catch {
    return NextResponse.redirect(`${APP_BASE_URL}/auth/login?error=exchange`);
  }

  const session: Session = {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    id_token: tokens.id_token,
    expires_at: Date.now() + (tokens.expires_in ?? 300) * 1000,
  };

  const response = NextResponse.redirect(APP_BASE_URL);
  response.cookies.set(SESSION_COOKIE, await seal({ ...session }, SESSION_MAX_AGE_SECONDS), {
    httpOnly: true,
    secure: APP_BASE_URL.startsWith("https"),
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
  response.cookies.delete(TX_COOKIE); // one-time secrets, spent
  return response;
}
