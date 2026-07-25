import { NextRequest, NextResponse } from "next/server";

import { getAccessToken } from "@/lib/auth";
import { API_URL } from "@/lib/config";

// Server side of the GAEB converter, step 2. Takes the confirmed positions as JSON, calls the
// backend's /gaeb/export, and streams the .x84 bytes back as a download — carrying the backend's
// Content-Disposition so the browser saves it with the right filename. A backend 422 (an unpriced
// position) is passed through as JSON so the page can show why nothing was produced.

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid request body" }, { status: 400 });
  }

  let token: string;
  try {
    token = await getAccessToken();
  } catch (error) {
    return NextResponse.json(
      { error: (error as Error).message, kind: "auth" },
      { status: 503 },
    );
  }

  const backend = await fetch(`${API_URL}/gaeb/export`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!backend.ok) {
    const text = await backend.text();
    let detail = text;
    try {
      detail = (JSON.parse(text) as { detail?: string }).detail ?? text;
    } catch {
      // not JSON — keep the raw text
    }
    const kind = backend.status === 401 || backend.status === 403 ? "auth" : "server";
    return NextResponse.json({ error: detail, kind }, { status: backend.status });
  }

  const bytes = await backend.arrayBuffer();
  const disposition =
    backend.headers.get("content-disposition") ?? 'attachment; filename="angebot.x84"';
  return new NextResponse(bytes, {
    status: 200,
    headers: {
      "Content-Type": "application/xml",
      "Content-Disposition": disposition,
    },
  });
}
