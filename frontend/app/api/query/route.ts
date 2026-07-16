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

  try {
    const token = await getAccessToken();
    const answer = await postQuery(body, token);
    return NextResponse.json(answer);
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json({ error: error.detail }, { status: error.status });
    }
    // Keycloak unreachable, network, etc. Report it plainly — never as an empty answer.
    return NextResponse.json(
      { error: (error as Error).message },
      { status: 502 },
    );
  }
}
