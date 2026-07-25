import { NextRequest, NextResponse } from "next/server";

import { ApiError, parseGaeb } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";

// Server side of the GAEB converter, step 1. The browser posts the LV file as multipart FormData;
// this attaches the real token (kept server-side) and forwards it to the backend's /gaeb/parse,
// which reads it into positions. Nothing is generated here — parsing and exporting are separate on
// purpose, so a person confirms the positions before a .x84 is produced.

export async function POST(request: NextRequest) {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "invalid upload" }, { status: 400 });
  }

  if (!form.get("file")) {
    return NextResponse.json({ error: "no file in the upload" }, { status: 400 });
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
    const boq = await parseGaeb(form, token);
    return NextResponse.json(boq);
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
