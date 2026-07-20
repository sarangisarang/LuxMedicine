import { NextRequest, NextResponse } from "next/server";

import { ApiError, postQuery, type QueryRequest } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

// The server side of a question. The browser posts here; this handler attaches the real
// token (server-side, so it never reaches the client) and forwards to the backend. Keeping
// it thin is deliberate — no interpretation, no reshaping of the answer. The frontend is a
// faithful renderer of what the backend returns, and that starts here.

export async function POST(request: NextRequest) {
  let body: QueryRequest;
  try {
    body = (await request.json()) as QueryRequest;
  } catch {
    return NextResponse.json({ error: "invalid request body" }, { status: 400 });
  }

  if (!body?.question?.trim()) {
    return NextResponse.json({ error: "a question is required" }, { status: 400 });
  }

  // Getting a token is a distinct failure from the backend refusing one or being down, and a
  // clinician needs to be told which: "sign in again" and "the server is unreachable" are
  // different actions. `kind` carries that distinction to the UI so it does not have to guess
  // from a status code.
  let token: string;
  try {
    token = await getAccessToken();
  } catch (error) {
    // Could not obtain a token at all — the login service (Keycloak) is down or the credentials
    // are wrong. An auth-infrastructure problem, not a rejected question.
    return NextResponse.json(
      { error: (error as Error).message, kind: "auth" },
      { status: 503 },
    );
  }

  try {
    const answer = await postQuery(body, token);
    return NextResponse.json(answer);
  } catch (error) {
    if (error instanceof ApiError) {
      // 401/403: the backend rejected the token (expired, wrong audience). Still an auth
      // problem, but a different one — the token exists and was refused.
      const kind = error.status === 401 || error.status === 403 ? "auth" : "server";
      return NextResponse.json({ error: error.detail, kind }, { status: error.status });
    }
    // Backend unreachable (network, DNS, connection refused). Report it plainly — never as an
    // empty answer, which would read as "the guidelines are silent".
    return NextResponse.json(
      { error: (error as Error).message, kind: "server" },
      { status: 502 },
    );
  }
}
