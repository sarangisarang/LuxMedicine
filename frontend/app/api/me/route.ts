import { NextResponse } from "next/server";

import { getAccessToken, NotAuthenticated } from "@/lib/auth";

// Who is logged in, for the UI to show — and the quickest proof that the OIDC session yields a
// real per-user token. It reads the claims of the access token the server holds; it does NOT
// verify them (the backend does that on every real call) and does NOT reach the backend, so it
// works regardless of where the API is. 401 when there is no session.
export async function GET() {
  try {
    const token = await getAccessToken();
    const claims = JSON.parse(
      Buffer.from(token.split(".")[1], "base64url").toString("utf8"),
    ) as { sub?: string; clinic_id?: string; name?: string; email?: string };
    return NextResponse.json({
      sub: claims.sub,
      clinic_id: claims.clinic_id,
      name: claims.name,
      email: claims.email,
    });
  } catch (error) {
    if (error instanceof NotAuthenticated) {
      return NextResponse.json({ error: "not authenticated" }, { status: 401 });
    }
    throw error;
  }
}
