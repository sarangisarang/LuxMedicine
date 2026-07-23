"use client";

import type { Citation } from "@/lib/api";
import { pageDisplay } from "@/lib/wording";

import { QuoteTranslation } from "./QuoteTranslation";
import { useT } from "./LanguageContext";

// One verbatim quote and its provenance. The quote is the ONLY clinical text in the whole
// schema — rendered exactly, never truncated with an ellipsis or joined to another span
// (two spans are two citations). Everything below the rule is provenance, not content.
//
// The page reference is where #35 will attach: clicking it opens the source PDF at
// page_start. It is a button already, so the jump-to-page viewer slots in here without
// touching the rest of the answer view.
export function CitationItem({
  citation,
  onOpenSource,
  fromDamagedPage = false,
}: {
  citation: Citation;
  onOpenSource?: (c: Citation) => void;
  // The page THIS quote came from lost some of its text at extraction. Not the same warning
  // as the group's footer, which names the document's damaged pages as a set: a page that
  // vanished entirely produces no quote at all, so the footer describes an absence. This
  // describes a presence — a page read in part, whose surviving chunks answer while the
  // qualifying sentence beside them may be gone. That is the quote a clinician would trust
  // most and should trust least, so the mark belongs against the quote itself.
  fromDamagedPage?: boolean;
}) {
  const t = useT();
  return (
    <li className="px-4 py-3">
      <blockquote
        className={
          fromDamagedPage
            ? "border-l-2 border-amber-400 pl-3 text-sm leading-relaxed dark:border-amber-600"
            : "border-l-2 border-neutral-300 pl-3 text-sm leading-relaxed dark:border-neutral-600"
        }
      >
        {citation.quote}
      </blockquote>
      {fromDamagedPage && (
        <p
          role="status"
          className="mt-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
        >
          {t.damagedPageQuote}
        </p>
      )}
      <p className="mt-1.5 text-xs text-neutral-500">
        {citation.document_title}
        {" · "}
        {onOpenSource ? (
          <button
            type="button"
            onClick={() => onOpenSource(citation)}
            className="underline decoration-dotted underline-offset-2 hover:text-neutral-800 dark:hover:text-neutral-200"
            title="Open the source page"
          >
            {pageDisplay(citation)}
          </button>
        ) : (
          pageDisplay(citation)
        )}
        {citation.section ? ` · ${citation.section}` : ""}
      </p>

      {/* Below the quote and the provenance, never above: the guideline's words come first,
          and a translation is a reading aid the clinician asks for. */}
      <QuoteTranslation quote={citation.quote} />
    </li>
  );
}
