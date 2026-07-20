// The backend contract, in the frontend. Every type here is generated from the backend's
// own OpenAPI schema (npm run gen:types), never hand-written — so AnswerPayload, the
// no_answer_reason enum, rejected_citations and unreadable_pages cannot silently drift out
// of sync with app/schemas/answer.py. If the backend changes the contract, `tsc` fails here
// before a screen renders the wrong shape.

import type { components } from "./api-types";
import { API_URL } from "./config";

export type QueryRequest = components["schemas"]["QueryRequest"];
export type QueryResponse = components["schemas"]["QueryResponse"];
export type TranslateRequest = components["schemas"]["TranslateRequest"];
export type TranslateResponse = components["schemas"]["TranslateResponse"];
export type AnswerPayload = components["schemas"]["AnswerPayload"];
export type SourceGroup = components["schemas"]["SourceGroup"];
export type Citation = components["schemas"]["Citation"];
export type ConflictFinding = components["schemas"]["ConflictFinding"];
export type NoAnswerReason = components["schemas"]["NoAnswerReason"];
export type RegisterRequest = components["schemas"]["RegisterRequest"];
export type RegisterResponse = components["schemas"]["RegisterResponse"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`backend ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

/**
 * POST /queries — ask the corpus. The token is the ONLY source of the clinician's identity
 * and clinic (the tenant boundary RLS filters on); the request body carries no actor_id by
 * design (see backend/app/api/queries.py). Runs server-side so the token never reaches the
 * browser.
 */
export async function postQuery(
  body: QueryRequest,
  token: string,
): Promise<QueryResponse> {
  const response = await fetch(`${API_URL}/queries`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    // The audit row must reflect a real request; never serve a cached answer.
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as QueryResponse;
}

/**
 * POST /translate — a reading aid for ONE quote, asked for explicitly.
 *
 * Deliberately not part of postQuery: an answer is verbatim spans and provenance, and that is
 * what the audit row records. A translation is machine output #19 cannot validate, so it is
 * fetched separately, shown beside the original, and labelled as what it is.
 */
export async function postTranslation(
  body: TranslateRequest,
  token: string,
): Promise<TranslateResponse> {
  const response = await fetch(`${API_URL}/translate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as TranslateResponse;
}

/**
 * POST /register — redeem an invite and create the account it grants.
 *
 * The one backend call made with NO token: a newcomer has no account yet, so the invite code is
 * the gate. Errors carry through with their status so the page can tell a bad code (400) from a
 * taken username (409) from a provider outage (502/503) — the same "say which failure" discipline
 * as a query.
 */
export async function postRegister(body: RegisterRequest): Promise<RegisterResponse> {
  const response = await fetch(`${API_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as RegisterResponse;
}
