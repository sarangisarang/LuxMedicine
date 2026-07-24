import { NextRequest, NextResponse } from "next/server";

import { ApiError, postIngestBatch } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

// The server side of a corpus upload. The browser posts the folder here as multipart FormData;
// this handler attaches the real token (server-side, never in the browser) and forwards it to the
// backend's admin-only POST /ingest/batches. Thin on purpose, like the query route: the multipart
// body is passed through unchanged, and the backend is the only place that decides who may upload.

export async function POST(request: NextRequest) {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "invalid upload" }, { status: 400 });
  }

  if (form.getAll("files").length === 0) {
    return NextResponse.json({ error: "no files in the upload" }, { status: 400 });
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

  try {
    const accepted = await postIngestBatch(form, token);
    return NextResponse.json(accepted, { status: 202 });
  } catch (error) {
    if (error instanceof ApiError) {
      // 401/403 is auth (no token / not a clinic-admin); everything else the backend said, incl.
      // 422 for a missing provenance note, is passed through with its status so the page can show it.
      const kind = error.status === 401 || error.status === 403 ? "auth" : "server";
      return NextResponse.json({ error: error.detail, kind }, { status: error.status });
    }
    return NextResponse.json(
      { error: (error as Error).message, kind: "server" },
      { status: 502 },
    );
  }
}
