import { NextRequest, NextResponse } from "next/server";

import { ApiError, listDocuments } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

// The server side of the corpus sidebar. The browser asks for the active documents in a sector;
// this attaches the token (server-side) and forwards to the backend's tenant-scoped GET /documents.

export async function GET(request: NextRequest) {
  const sector = request.nextUrl.searchParams.get("sector") ?? "medical";

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
    const list = await listDocuments(sector, token);
    return NextResponse.json(list);
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
