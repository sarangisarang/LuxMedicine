"use client";

import dynamic from "next/dynamic";
import { useState } from "react";

import { AnswerView } from "@/components/AnswerView";
import { CorpusList } from "@/components/CorpusList";
import { CorpusUpload } from "@/components/CorpusUpload";
import { LanguageProvider } from "@/components/LanguageContext";
import type { SourceTarget } from "@/components/PdfViewer";
import type { QueryResponse } from "@/lib/api";
import {
  QUERY_LANGUAGE,
  SECTORS,
  STRINGS,
  UI_LANGUAGES,
  type Sector,
  type UiLang,
} from "@/lib/i18n";

// react-pdf touches browser-only APIs (DOMMatrix, canvas) and must not render on the
// server. Loaded client-side only; this is the standard react-pdf + Next pattern.
const PdfViewer = dynamic(
  () => import("@/components/PdfViewer").then((m) => m.PdfViewer),
  { ssr: false },
);

export default function Home() {
  const [question, setQuestion] = useState("");
  // One control, two jobs: it localises the interface and it is the `language` hint recorded
  // on the query (never a corpus filter — a guideline in another language is still a source).
  const [lang, setLang] = useState<UiLang>("en");
  // Which corpus the question is asked of. Unlike `lang` this IS a filter — the backend
  // searches one sector and never both, so a clinical question cannot return a statute.
  // Defaults to medicine: that is what this system is, and the safe direction if the
  // control is ignored (a legal question against the clinical corpus finds nothing, which
  // is visible; the reverse would quote building-fee law to a clinician).
  const [sector, setSector] = useState<Sector>("medical");
  const [answer, setAnswer] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // What the source panel is showing: a cited quote, or a page listed as unreadable. Both
  // open the same viewer at the same document; only one of them has a quote to highlight.
  const [source, setSource] = useState<SourceTarget | null>(null);

  const t = STRINGS[lang];

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setAnswer(null);
    setSource(null);
    try {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, language: QUERY_LANGUAGE[lang], sector }),
      });
      const data = await res.json();
      if (!res.ok) {
        // The route tags the cause (see app/api/query/route.ts): an auth/token problem and the
        // server being down are different actions for the clinician, and neither is "no answer".
        if (data.kind === "auth") setError(t.errors.auth);
        else if (data.kind === "server") setError(t.errors.server);
        else setError(t.errors.request(data.error ?? `HTTP ${res.status}`));
      } else {
        setAnswer(data as QueryResponse);
      }
    } catch {
      // fetch itself threw — the frontend could not even reach its own API route (network,
      // the app not running). That is a server-reachability problem, not a rejected question.
      setError(t.errors.server);
    } finally {
      setLoading(false);
    }
  }

  return (
    <LanguageProvider lang={lang}>
      {/* Side-by-side (#35): the answer stays on the left while the source opens on the right,
          so a clinician reads our quote and the original page at once — verification without
          holding the text in short-term memory. */}
      <div className="flex min-h-screen">
        {/* Left sidebar: the searchable corpus for the selected sector, switching with it. */}
        <CorpusList sector={sector} lang={lang} />
        <main className="flex-1 px-6 py-12">
          <div className="mx-auto max-w-3xl">
            <div className="flex items-baseline justify-between">
              <h1 className="text-xl font-semibold tracking-tight">LuxMedicine</h1>
              {/* A full server round-trip, not a client call: /auth/logout ends the Keycloak
                  session too, so signing out is a real logout, not a dropped cookie. */}
              <a href="/auth/logout" className="text-xs text-neutral-500 underline">
                {t.signOut}
              </a>
            </div>
            <p className="mt-1 text-sm text-neutral-500">{t.tagline}</p>

            {/* Demo posture: inference runs on a non-EU endpoint (free Gemini) until Vertex-EU is
                wired. Remove this banner when ALLOW_NON_EU_INFERENCE is turned off and inference is
                EU-resident again — see backend Settings.allow_non_eu_inference. */}
            <p className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
              {t.demoNotice}
            </p>

            {/* Only in the legal corpus, and always — not once, not dismissible. The output there
                is verbatim statute text with a §-reference and a page number, which reads exactly
                like an answer to "what may I bill". Quoting §35 HOAI correctly says nothing about
                whether §35 applies to this contract, and nothing else on the page makes that gap
                visible. The second notice names a known hole rather than letting silence about the
                Honorartafeln read as the statute having nothing to say — see table_guard.py. */}
            {sector === "legal" && (
              <div className="mt-3 space-y-2">
                <p className="rounded-md border border-sky-300 bg-sky-50 p-3 text-xs text-sky-900 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200">
                  {t.legalNotice}
                </p>
                <p className="rounded-md border border-neutral-300 bg-neutral-50 p-3 text-xs text-neutral-700 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300">
                  {t.legalTableNotice}
                </p>
              </div>
            )}

            <form onSubmit={ask} className="mt-8 space-y-3">
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={t.placeholder}
                rows={3}
                className="w-full rounded-md border border-neutral-300 p-3 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
              />
              <div className="flex items-center gap-3">
                <select
                  aria-label={t.sectorLabel}
                  value={sector}
                  onChange={(e) => setSector(e.target.value as Sector)}
                  className="rounded-md border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
                >
                  {SECTORS.map((s) => (
                    <option key={s.code} value={s.code}>
                      {s.label[lang]}
                    </option>
                  ))}
                </select>
                <select
                  value={lang}
                  onChange={(e) => setLang(e.target.value as UiLang)}
                  className="rounded-md border border-neutral-300 px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
                >
                  {UI_LANGUAGES.map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.label}
                    </option>
                  ))}
                </select>
                <button
                  type="submit"
                  disabled={loading || !question.trim()}
                  className="rounded-md bg-neutral-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
                >
                  {loading ? t.searching : t.ask}
                </button>
              </div>
            </form>

            {/* Self-service corpus upload (admin-only; the backend enforces it and rejects a
                non-admin with 403). A collapsed section so it does not crowd the query screen. */}
            <CorpusUpload lang={lang} />

            {error && (
              <p className="mt-8 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
                {error}
              </p>
            )}

            {answer && (
              <AnswerView
                answer={answer.answer}
                onOpenSource={(c) => setSource({ kind: "citation", citation: c })}
                onOpenPage={(documentVersionId, documentTitle, page) =>
                  setSource({ kind: "page", documentVersionId, documentTitle, page })
                }
              />
            )}
          </div>
        </main>

        {source && (
          <aside className="sticky top-0 h-screen w-[45%] max-w-2xl border-l border-neutral-200 dark:border-neutral-800">
            {/* key per citation: a new selection remounts the viewer at the new cited page. */}
            <PdfViewer
              key={
                source.kind === "citation"
                  ? `${source.citation.document_version_id}:${source.citation.page_start}`
                  : `${source.documentVersionId}:${source.page}`
              }
              target={source}
              onClose={() => setSource(null)}
            />
          </aside>
        )}
      </div>
    </LanguageProvider>
  );
}
