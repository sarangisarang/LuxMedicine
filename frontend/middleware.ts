import { NextRequest, NextResponse } from "next/server";

// A page load with no session cookie goes to login. Presence check only — fast and edge-safe; the
// route handlers fully decrypt, validate, and refresh the session. The name is inlined rather than
// imported from lib/oidc so the heavy server-only OIDC modules never get pulled into the edge
// bundle.
// The session is split across numbered cookies (lux_session.0, .1, …) because a single cookie
// holding three JWTs exceeds the browser's ~4 KB limit and gets silently dropped (see lib/oidc).
// Presence of the first chunk is enough for this fast edge check; the routes reassemble and verify.
const SESSION_COOKIE_FIRST = "lux_session.0";

export function middleware(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE_FIRST)) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  url.pathname = "/auth/login";
  url.search = "";
  return NextResponse.redirect(url);
}

// Protect the app page. Public by design and NOT matched here: /auth/* (the login flow),
// /register (#51, invite-gated), /api/* (which answer with their own auth error), and static
// assets. Extend the matcher as authenticated pages are added.
export const config = {
  matcher: ["/"],
};
