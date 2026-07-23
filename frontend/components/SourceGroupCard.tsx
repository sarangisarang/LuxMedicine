"use client";

import type { Citation, SourceGroup } from "@/lib/api";
import { PAGES_SLOT } from "@/lib/i18n";

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
  onOpenPage,
}: {
  group: SourceGroup;
  onOpenSource?: (c: Citation) => void;
  onOpenPage?: (documentVersionId: string, documentTitle: string, page: number) => void;
}) {
  const t = useT();

  // The note reads "Pages 379, 381 … could not be read". Those numbers are the one thing a
  // clinician can act on — the schema comment says as much ("so a clinician can open the PDF
  // and look") — so each is a button that opens the viewer there. The sentence is split on
  // PAGES_SLOT rather than assembled from fragments, because the list sits in a different
  // place in each language and a hand-built sentence would be wrong in the one nobody here
  // reads. If a translation ever stops interpolating exactly once, `after` is undefined and
  // the fallback below prints the plain sentence rather than a mangled one.
  const [before, after] = t.unreadable(PAGES_SLOT).split(PAGES_SLOT);
  const splitCleanly = after !== undefined;

  // The group's own document. Every citation in it shares this version (SourceGroup carries
  // one `document_version_id`), so the unreadable pages belong to exactly this PDF.
  const documentTitle = group.citations[0]?.document_title ?? group.issuing_org;

  return (
    <article className="overflow-hidden rounded-lg border border-neutral-300 dark:border-neutral-700">
      <header className="flex items-baseline gap-2 border-b border-neutral-200 px-4 py-2.5 dark:border-neutral-800">
        <span className="text-sm font-semibold">{group.issuing_org}</span>
        <span className="text-xs text-neutral-500">{group.version_label}</span>
      </header>

      <StalenessBanner group={group} />

      <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
        {group.citations.map((c, i) => (
          <CitationItem
            key={i}
            citation={c}
            onOpenSource={onOpenSource}
            // THIS quote's own page lost content, which the footer's document-level list
            // cannot say. The danger is not the page that vanished — that one produces a
            // decline, and `incomplete_sources` now explains it. It is the page read only in
            // part: its chunks are in the index and answer, while the sentence that qualified
            // them may be in the half that was dropped. On NHLBI that is 73 pages —
            // partly-damaged rather than lost — and a quote from one of them looks complete
            // and is not. #48's shape exactly, so it is marked on the quote, not under it.
            fromDamagedPage={(group.unreadable_pages ?? []).includes(c.page_start)}
          />
        ))}
      </ul>

      {group.unreadable_pages && group.unreadable_pages.length > 0 && (
        // #41 — not a diagnostic, something the clinician is entitled to see next to the
        // quote: an equation or table on these pages could not be read, so "the guideline
        // does not say" and "we could not read where it says it" stay distinguishable.
        <p className="border-t border-neutral-200 px-4 py-2 text-xs text-neutral-500 dark:border-neutral-800">
          {onOpenPage && splitCleanly ? (
            <>
              {before}
              {group.unreadable_pages.map((p, i) => (
                <span key={p}>
                  {i > 0 && ", "}
                  <button
                    type="button"
                    onClick={() => onOpenPage(group.document_version_id, documentTitle, p)}
                    className="rounded underline decoration-dotted underline-offset-2 hover:text-neutral-800 focus:outline-none focus-visible:ring-1 focus-visible:ring-neutral-400 dark:hover:text-neutral-300"
                  >
                    {p}
                  </button>
                </span>
              ))}
              {after}
            </>
          ) : (
            t.unreadable(group.unreadable_pages.join(", "))
          )}
        </p>
      )}
    </article>
  );
}
