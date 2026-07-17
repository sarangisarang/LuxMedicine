import type { Citation } from "@/lib/api";
import { pageDisplay } from "@/lib/wording";

import { QuoteTranslation } from "./QuoteTranslation";

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
}: {
  citation: Citation;
  onOpenSource?: (c: Citation) => void;
}) {
  return (
    <li className="px-4 py-3">
      <blockquote className="border-l-2 border-neutral-300 pl-3 text-sm leading-relaxed dark:border-neutral-600">
        {citation.quote}
      </blockquote>
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
