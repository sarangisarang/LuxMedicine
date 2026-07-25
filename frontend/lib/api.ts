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

// Ingest types: hand-written for now because api-types.ts is regenerated from the backend's
// openapi.json (npm run gen:types) and this endpoint ships after. Replace with
// components["schemas"]["BatchStatus"] etc. once the schema is regenerated — the same
// no-hand-written-types discipline as everything above.
export type BatchFileStatus = {
  filename: string;
  parts: number;
  done_parts: number;
  version_ids: string[];
  error: string | null;
};

export type BatchStatus = {
  job_id: string;
  sector: string;
  status: "queued" | "running" | "done" | "failed";
  phase: string;
  percent: number;
  files: BatchFileStatus[];
  error: string | null;
};

export type BatchAccepted = { job_id: string };

/**
 * POST /ingest/batches — upload a folder of guideline PDFs for the corpus. Multipart, so the body
 * is a FormData (files + issuing_org + version_label + provenance); the Content-Type header is left
 * unset on purpose so fetch writes the multipart boundary itself. Admin-only, enforced by the
 * backend. Returns a job id to poll — indexing runs in the background.
 */
export async function postIngestBatch(form: FormData, token: string): Promise<BatchAccepted> {
  const response = await fetch(`${API_URL}/ingest/batches`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as BatchAccepted;
}

/** GET /ingest/batches/{id} — poll an upload's phase and 0-100% progress. */
export async function getIngestStatus(jobId: string, token: string): Promise<BatchStatus> {
  const response = await fetch(`${API_URL}/ingest/batches/${jobId}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as BatchStatus;
}

// Hand-written until gen:types picks up the new /documents list schema — same TODO as the ingest types.
export type DocumentSummary = {
  title: string;
  issuing_org: string;
  region: string | null;
  version_label: string;
  published_at: string | null;
  status: string;
};

export type DocumentList = { documents: DocumentSummary[] };

/** GET /documents?sector= — the active (searchable) corpus for one sector, for the sidebar. */
export async function listDocuments(sector: string, token: string): Promise<DocumentList> {
  const response = await fetch(`${API_URL}/documents?sector=${encodeURIComponent(sector)}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as DocumentList;
}

// --- GAEB converter (Baurecht): any LV file -> editable positions -> downloadable .x84 -----------

/** One row of the converter table, in the order the specification asks for:
 *  KG → KG level 2 → 3 → 4 → Positionsnummer → Leistungstext → Menge → Einheit → Teilbetrag → EP →
 *  Gesamt EUR. Every value is the string the source file printed — nothing here is computed. */
export type GaebPosition = {
  /** The DIN 276 cost-group path this position was printed under, outermost first (up to 4 levels). */
  kg: string[];
  oz: string;
  short_text: string;
  quantity: string;
  unit: string;
  teilbetrag: string | null;
  unit_price: string | null;
  total: string | null;
  long_text: string | null;
  section: string;
};

/** One row of the source document, mirroring its columns. `kind` says what the row is, which is
 *  what lets the table be rendered the way the document reads. Every value is the source's own
 *  text — nothing is computed. */
export type GaebEntry = {
  kind: "kg" | "entry" | "position";
  number: string;
  text: string;
  menge_einheit: string;
  teilbetrag_ep: string;
  gesamt: string;
  level: number;
  kg: string[];
  long_text: string | null;
};

export type GaebBoQ = {
  project_name: string;
  currency: string;
  positions: GaebPosition[];
  /** The document as it stands, in order. This is what the table shows. */
  entries: GaebEntry[];
};

/**
 * POST /gaeb/parse — read an uploaded LV (Excel, CSV, or an existing GAEB file) into positions the
 * user then verifies and prices. Multipart, so the Content-Type is left unset for fetch to write the
 * boundary. Nothing is exported here; that is the explicit second step.
 */
export async function parseGaeb(form: FormData, token: string): Promise<GaebBoQ> {
  const response = await fetch(`${API_URL}/gaeb/parse`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ApiError(response.status, await response.text());
  }
  return (await response.json()) as GaebBoQ;
}
