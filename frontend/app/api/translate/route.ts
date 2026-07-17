import { NextRequest, NextResponse } from "next/server";

import { ApiError, postTranslation, type TranslateRequest } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

// The server side of "translate this quote". Same shape as /api/query: the token is attached
// here and never reaches the browser, and no CORS is opened.
//
// A separate route from /api/query on purpose — see lib/api.ts. The answer and its audit row
// stay verbatim; this is a reading aid a clinician asks for, one quote at a time.

export async function POST(request: NextRequest) {
  let body: TranslateRequest;
  try {
    body = (await request.json()) as TranslateRequest;
  } catch {
    return NextResponse.json({ error: "invalid request body" }, { status: 400 });
  }

  if (!body?.quote?.trim() || !body?.target_language) {
    return NextResponse.json(
      { error: "a quote and a target language are required" },
      { status: 400 },
    );
  }

  try {
    const token = await getAccessToken();
    return NextResponse.json(await postTranslation(body, token));
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json({ error: error.detail }, { status: error.status });
    }
    return NextResponse.json({ error: (error as Error).message }, { status: 502 });
  }
}
