"use client";

import { useState } from "react";

import type { TranslateResponse } from "@/lib/api";

import { useT } from "./LanguageContext";

// Languages a clinician here actually reads. The list is short on purpose: every entry is a
// language someone has to be able to check the output in.
const LANGUAGES: { code: string; label: string }[] = [
  { code: "German", label: "Deutsch" },
  { code: "Georgian", label: "ქართული" },
  { code: "English", label: "English" },
];

/**
 * A reading aid under one quote — asked for, never volunteered.
 *
 * The guideline's own words are above this and stay there. What appears here is machine
 * output that #19 cannot validate: a translation is not a verbatim span, so nothing checks
 * it against the source the way every quote is checked. The label is not decoration — it is
 * the only thing standing between "the guideline says" and "a model said".
 *
 * The failure that matters is quiet: drop the "not" from "do not routinely advise people
 * with heart failure to restrict their sodium" and the sentence says the opposite, under a
 * real page number. So: subordinate type, an explicit marker, and the original always visible
 * directly above for comparison.
 */
export function QuoteTranslation({ quote }: { quote: string }) {
  const t = useT();
  const [result, setResult] = useState<TranslateResponse | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function translate(language: string) {
    setLoading(language);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quote, target_language: language }),
      });
      const data = await res.json();
      if (!res.ok) {
        // 429 is the quota, not a fault: it gets its own words so nobody debugs a budget.
        setError(
          res.status === 429
            ? t.translation.rateLimited
            : (data.error ?? `${t.translation.failed} (${res.status})`),
        );
      }
      else setResult(data as TranslateResponse);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-neutral-400">{t.translation.label}</span>
        {LANGUAGES.map((l) => (
          <button
            key={l.code}
            type="button"
            onClick={() => translate(l.code)}
            disabled={loading !== null}
            className="rounded border border-neutral-300 px-1.5 py-0.5 text-[11px] text-neutral-600 hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800"
          >
            {loading === l.code ? "…" : l.label}
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-1.5 text-[11px] text-red-700 dark:text-red-300">{error}</p>
      )}

      {result && (
        <figure className="mt-2 rounded border border-dashed border-neutral-300 bg-neutral-50 p-2.5 dark:border-neutral-700 dark:bg-neutral-900/60">
          {/* Says what this is before it can be read as the guideline speaking. */}
          <figcaption className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-500">
            <span aria-hidden>⚠</span>
            {t.translation.banner(result.target_language)}
          </figcaption>
          <p className="text-sm text-neutral-700 dark:text-neutral-300">{result.text}</p>
          <p className="mt-1.5 text-[10px] text-neutral-500">{t.translation.footnote}</p>
        </figure>
      )}
    </div>
  );
}
