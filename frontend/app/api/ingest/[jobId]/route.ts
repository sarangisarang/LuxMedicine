import { NextRequest, NextResponse } from "next/server";

import { ApiError, getIngestStatus } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

// The server side of the upload progress bar. The browser polls this same-origin route; the token
// is attached here and never leaves the server, exactly as the query and pdf routes work.

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;

  let token: string;
  try {
    token = await getAccessToken();
  } catch (error) {
    return NextResponse.json(
      { error: (error as Error).message, kind: "auth" },
      { status: 503 },
    );
  }

  try {
    const status = await getIngestStatus(jobId, token);
    return NextResponse.json(status);
  } catch (error) {
    if (error instanceof ApiError) {
      const kind = error.status === 401 || error.status === 403 ? "auth" : "server";
      return NextResponse.json({ error: error.detail, kind }, { status: error.status });
    }
    return NextResponse.json(
      { error: (error as Error).message, kind: "server" },
      { status: 502 },
    );
  }
}
