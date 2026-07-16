import { NextRequest } from "next/server";

import { API_URL } from "@/lib/config";
import { getAccessToken } from "@/lib/auth";

// The server side of the PDF viewer. The browser (react-pdf) fetches this same-origin route,
// never the backend directly — so no CORS is opened and the token never leaves the server,
// exactly as the query flow works. This handler attaches the token and forwards to the
// backend's tenant-guarded GET /documents/{id}/pdf.
//
// Range is forwarded both ways: react-pdf asks for byte ranges to load a large PDF a piece
// at a time, the backend's FileResponse answers 206 with a Content-Range, and passing the
// header through in both directions is what keeps that working — the body is streamed, never
// buffered, so a 100MB guideline flows through without landing in this process's memory.

const PASSTHROUGH_HEADERS = [
  "content-type",
  "content-length",
  "content-range",
  "accept-ranges",
  "content-disposition",
  "cache-control",
];

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ versionId: string }> },
) {
  const { versionId } = await params;

  let token: string;
  try {
    token = await getAccessToken();
  } catch (error) {
    return new Response((error as Error).message, { status: 502 });
  }

  const range = request.headers.get("range");
  const upstream = await fetch(`${API_URL}/documents/${versionId}/pdf`, {
    headers: {
      Authorization: `Bearer ${token}`,
      ...(range ? { Range: range } : {}),
    },
    cache: "no-store",
  });

  const headers = new Headers();
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }

  // Stream the body through unchanged; preserve 200 vs 206 (partial) and error statuses.
  return new Response(upstream.body, { status: upstream.status, headers });
}
