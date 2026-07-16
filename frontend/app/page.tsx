"use client";

import { useState } from "react";

import { AnswerView } from "@/components/AnswerView";
import type { QueryResponse } from "@/lib/api";

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

      {answer && <AnswerView answer={answer.answer} />}
    </main>
  );
}
