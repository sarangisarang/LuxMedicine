"use client";

import type { Citation, SourceGroup } from "@/lib/api";

import { CitationItem } from "./CitationItem";
import { useT } from "./LanguageContext";
import { StalenessBanner } from "./StalenessBanner";

// #33 — one issuing organisation's guidance, in its own words. Grouped by organisation so a
// clinician sees WHOSE guidance this is before reading it, and so the layout never blends
// two bodies' text into one apparent answer. Ordering of citations is the order they were
// retrieved — the card adds no ranking of its own.
export function SourceGroupCard({
  group,
  onOpenSource,
}: {
  group: SourceGroup;
  onOpenSource?: (c: Citation) => void;
}) {
  const t = useT();

  return (
    <article className="overflow-hidden rounded-lg border border-neutral-300 dark:border-neutral-700">
      <header className="flex items-baseline gap-2 border-b border-neutral-200 px-4 py-2.5 dark:border-neutral-800">
        <span className="text-sm font-semibold">{group.issuing_org}</span>
        <span className="text-xs text-neutral-500">{group.version_label}</span>
      </header>

      <StalenessBanner group={group} />

      <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
        {group.citations.map((c, i) => (
          <CitationItem key={i} citation={c} onOpenSource={onOpenSource} />
        ))}
      </ul>

      {group.unreadable_pages && group.unreadable_pages.length > 0 && (
        // #41 — not a diagnostic, something the clinician is entitled to see next to the
        // quote: an equation or table on these pages could not be read, so "the guideline
        // does not say" and "we could not read where it says it" stay distinguishable.
        <p className="border-t border-neutral-200 px-4 py-2 text-xs text-neutral-500 dark:border-neutral-800">
          {t.unreadable(group.unreadable_pages.join(", "))}
        </p>
      )}
    </article>
  );
}
