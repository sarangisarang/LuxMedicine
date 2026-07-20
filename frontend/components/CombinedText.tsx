"use client";

import type { AnswerPayload, Citation } from "@/lib/api";

import { useT } from "./LanguageContext";

// The quotes gathered into one block, to read the whole answer in one place.
//
// It is deliberately NOT a summary, and this file is the one place that promise could be
// broken, so it keeps the guardrails explicit:
//   - it renders `citation.quote` verbatim and generates no text of its own, so #19's guarantee
//     still holds for every word here;
//   - it never joins the quotes into flowing prose. Each stays a separate block with its own
//     page, because gluing distant spans into one paragraph reads as a single statement the
//     source never made — the exact failure the extractor is forbidden from committing.
// So this is a reading convenience over the same verbatim material, not a second, softer answer.
export function CombinedText({ answer }: { answer: AnswerPayload }) {
  const t = useT();

  const items: Citation[] = (answer.groups ?? []).flatMap((g) => g.citations);
  // A "combined" block for a single quote just repeats the card above it.
  if (items.length < 2) return null;

  const pages = (c: Citation) =>
    c.page_start === c.page_end ? `p. ${c.page_start}` : `pp. ${c.page_start}–${c.page_end}`;

  return (
    <section
      aria-label={t.combined.heading}
      className="rounded-lg border border-neutral-300 dark:border-neutral-700"
    >
      <header className="border-b border-neutral-200 px-4 py-2.5 dark:border-neutral-800">
        <h2 className="text-sm font-semibold">{t.combined.heading}</h2>
        <p className="mt-1 text-xs text-neutral-500">{t.combined.note}</p>
      </header>

      <ol className="divide-y divide-neutral-100 dark:divide-neutral-800">
        {items.map((c, i) => (
          <li key={i} className="px-4 py-3">
            {/* Verbatim, whitespace preserved as extracted — the record, unaltered. */}
            <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{c.quote}</p>
            <p className="mt-1 text-xs text-neutral-500">
              {t.combined.pageRef(c.issuing_org, pages(c))}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}
