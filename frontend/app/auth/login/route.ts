import { NextResponse } from "next/server";
import * as client from "openid-client";

import { APP_BASE_URL } from "@/lib/config";
import {
  getOidcConfig,
  seal,
  TX_COOKIE,
  TX_MAX_AGE_SECONDS,
  type LoginTx,
} from "@/lib/oidc";

// Start of login: mint the one-request secrets (PKCE verifier, state, nonce), stash them in a
// short-lived encrypted cookie, and send the browser to Keycloak's authorization endpoint. Nothing
// sensitive is in the URL — the verifier stays in the cookie and is checked at the callback.
export async function GET() {
  const config = await getOidcConfig();

  const codeVerifier = client.randomPKCECodeVerifier();
  const codeChallenge = await client.calculatePKCECodeChallenge(codeVerifier);
  const state = client.randomState();
  const nonce = client.randomNonce();

  const authorizationUrl = client.buildAuthorizationUrl(config, {
    redirect_uri: `${APP_BASE_URL}/auth/callback`,
    scope: "openid profile email",
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
    state,
    nonce,
  });

  const tx: LoginTx = { state, nonce, code_verifier: codeVerifier };
  const response = NextResponse.redirect(authorizationUrl.href);
  response.cookies.set(TX_COOKIE, await seal({ ...tx }, TX_MAX_AGE_SECONDS), {
    httpOnly: true,
    secure: APP_BASE_URL.startsWith("https"),
    sameSite: "lax", // must survive the top-level GET redirect back from Keycloak
    path: "/",
    maxAge: TX_MAX_AGE_SECONDS,
  });
  return response;
}
