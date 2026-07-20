import { NextRequest, NextResponse } from "next/server";
import * as client from "openid-client";

import { APP_BASE_URL } from "@/lib/config";
import {
  clearSessionCookies,
  getOidcConfig,
  readSessionCookie,
  unseal,
  type Session,
} from "@/lib/oidc";

// End the session here AND at Keycloak, so a logout is a real logout and not just a dropped cookie
// with a live SSO session behind it. The id_token_hint lets Keycloak end the right session without
// asking the user to confirm.
export async function GET(request: NextRequest) {
  const config = await getOidcConfig();
  const session = await unseal<Session>(
    readSessionCookie((name) => request.cookies.get(name)?.value),
  );

  const target = session?.id_token
    ? client.buildEndSessionUrl(config, {
        post_logout_redirect_uri: APP_BASE_URL,
        id_token_hint: session.id_token,
      }).href
    : APP_BASE_URL;

  const response = NextResponse.redirect(target);
  clearSessionCookies((name) => response.cookies.delete(name));
  return response;
}
