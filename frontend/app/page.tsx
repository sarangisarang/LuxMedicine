"use client";

import { useState } from "react";

import type { Citation, QueryResponse, SourceGroup } from "@/lib/api";

// The human wording for an empty answer. The backend deliberately returns only an enum
// (NoAnswerReason) and no prose — "the wording a clinician reads lives in the UI, where a
// human chose it" (app/schemas/answer.py). The three are kept distinct on purpose: "the
// corpus has nothing" is a fact about the guidelines; "verification failed" is us
// malfunctioning, and must never read as an absence of guidance.
const NO_ANSWER_WORDING: Record<string, string> = {
  no_relevant_sources: "No guideline in the corpus covers this question.",
  sources_do_not_answer:
    "Guidelines were found, but none of their passages answer this question.",
  verification_failed:
    "An answer was produced but could not be verified against its source, so it is being withheld. This is a system fault, not an absence of guidance.",
};

function pageDisplay(c: Citation): string {
  return c.page_start === c.page_end
    ? `p. ${c.page_start}`
    : `pp. ${c.page_start}-${c.page_end}`;
}

export default function Home() {
  const [question, setQuestion] = useState("");
  const [language, setLanguage] = useState("en");
  const [answer, setAnswer] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, language }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? `request failed (${res.status})`);
      } else {
        setAnswer(data as QueryResponse);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <h1 className="text-xl font-semibold tracking-tight">LuxMedicine</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Clinical guideline search. Answers are the guideline&apos;s own words, cited to the
        page — never a summary or a recommendation.
      </p>

      {/* #32 — the query interface. Language is a hint recorded on the query, never a filter. */}
      <form onSubmit={ask} className="mt-8 space-y-3">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. How is chronic kidney disease classified by GFR?"
          rows={3}
          className="w-full rounded-md border border-neutral-300 p-3 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
        />
        <div className="flex items-center gap-3">
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="rounded-md border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          >
            <option value="en">English</option>
            <option value="ka">ქართული</option>
            <option value="de">Deutsch</option>
            <option value="fr">Français</option>
          </select>
          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="rounded-md bg-neutral-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
          >
            {loading ? "Searching…" : "Ask"}
          </button>
        </div>
      </form>

      {error && (
        <p className="mt-8 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          {error}
        </p>
      )}

      {answer && <Answer payload={answer} />}
    </main>
  );
}

// Placeholder renderer — enough to prove the contract flows and to show the honest signals
// (no-answer reason, rejected count, superseded, unreadable pages). The real #33 grouped
// view and #35 jump-to-page PDF viewer are the next step; this is the base structure.
function Answer({ payload }: { payload: QueryResponse }) {
  const a = payload.answer;

  if (!a.groups || a.groups.length === 0) {
    return (
      <section className="mt-8 rounded-md border border-neutral-300 p-4 dark:border-neutral-700">
        <p className="text-sm text-neutral-700 dark:text-neutral-300">
          {a.no_answer_reason
            ? NO_ANSWER_WORDING[a.no_answer_reason]
            : "No answer."}
        </p>
      </section>
    );
  }

  return (
    <section className="mt-8 space-y-6">
      {(a.rejected_citations ?? 0) > 0 && (
        <p className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {a.rejected_citations} quote(s) were rejected as unverifiable and are not shown.
        </p>
      )}
      {a.groups.map((g, i) => (
        <Group key={i} group={g} />
      ))}
    </section>
  );
}

// #33 — whose guidance it is, before the text is read. #36 — the staleness banner.
function Group({ group }: { group: SourceGroup }) {
  return (
    <article className="rounded-md border border-neutral-300 dark:border-neutral-700">
      <header className="border-b border-neutral-200 px-4 py-2 dark:border-neutral-800">
        <span className="text-sm font-semibold">{group.issuing_org}</span>
        <span className="ml-2 text-xs text-neutral-500">{group.version_label}</span>
      </header>

      {group.is_superseded && (
        <p className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          This edition has been superseded
          {group.superseding_version_label
            ? ` by ${group.superseding_version_label}.`
            : "."}
        </p>
      )}

      <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
        {group.citations.map((c, i) => (
          <li key={i} className="px-4 py-3">
            {/* quote is the only clinical text — verbatim, never paraphrased. */}
            <blockquote className="border-l-2 border-neutral-300 pl-3 text-sm dark:border-neutral-600">
              {c.quote}
            </blockquote>
            <p className="mt-1 text-xs text-neutral-500">
              {c.document_title} · {pageDisplay(c)}
              {c.section ? ` · ${c.section}` : ""}
            </p>
          </li>
        ))}
      </ul>

      {group.unreadable_pages && group.unreadable_pages.length > 0 && (
        <p className="border-t border-neutral-200 px-4 py-2 text-xs text-neutral-500 dark:border-neutral-800">
          Pages {group.unreadable_pages.join(", ")} of this document could not be read and
          are not reflected above.
        </p>
      )}
    </article>
  );
}
