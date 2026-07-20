import { NextRequest, NextResponse } from "next/server";

// A page load with no session cookie goes to login. Presence check only — fast and edge-safe; the
// route handlers fully decrypt, validate, and refresh the session. The name is inlined rather than
// imported from lib/oidc so the heavy server-only OIDC modules never get pulled into the edge
// bundle.
const SESSION_COOKIE = "lux_session";

export function middleware(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) {
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
