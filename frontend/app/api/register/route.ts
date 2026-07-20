import { NextRequest, NextResponse } from "next/server";

import { ApiError, postRegister, type RegisterRequest } from "@/lib/api";

// The server side of registration. Unlike /api/query it attaches NO token — /register is the one
// unauthenticated write, gated by the invite code, so a newcomer with no account can reach it.
// This handler stays thin: forward to the backend and translate its status into a `kind` the page
// maps to a message, the same pattern as the query route.

export async function POST(request: NextRequest) {
  let body: RegisterRequest;
  try {
    body = (await request.json()) as RegisterRequest;
  } catch {
    return NextResponse.json({ error: "invalid request body", kind: "validation" }, { status: 400 });
  }

  try {
    const result = await postRegister(body);
    return NextResponse.json(result, { status: 201 });
  } catch (error) {
    if (error instanceof ApiError) {
      // 400 invalid/expired code, 409 username taken, 422 bad field, 5xx provider — each a
      // different thing for the newcomer to do, carried as `kind`.
      const kind =
        error.status === 400
          ? "code"
          : error.status === 409
            ? "username"
            : error.status === 422
              ? "validation"
              : "server";
      return NextResponse.json({ error: error.detail, kind }, { status: error.status });
    }
    // The backend was unreachable from this app.
    return NextResponse.json(
      { error: (error as Error).message, kind: "server" },
      { status: 502 },
    );
  }
}
