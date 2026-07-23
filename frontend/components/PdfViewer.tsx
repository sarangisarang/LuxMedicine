"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import type { Citation } from "@/lib/api";
import { pageDisplay } from "@/lib/wording";

import { useT } from "./LanguageContext";

// Self-hosted worker (public/pdf.worker.min.mjs), synced from the installed pdfjs-dist by
// scripts/sync-pdf-worker.mjs so its version always matches react-pdf's bundled pdfjs — a
// mismatch is the "API version does not match Worker version" crash. Served from our own
// origin, never a CDN, for the same no-third-party reason the rest of the stack self-hosts.
pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

// What the viewer was asked to show. Two cases, and they are NOT the same thing:
//
//   - `citation` — a quote the answer used. There is text to highlight and the header may
//     honestly say "cited".
//   - `page` — a page listed as unreadable (#41). Nothing was quoted from it; that is the
//     reason to open it. Modelling this as a Citation with a blank quote was the shorter
//     route and is the wrong one: the viewer would announce a page as cited when the whole
//     point is that this system could not read it, and the highlighter would run against a
//     quote that does not exist. The union makes the difference unrepresentable-away.
export type SourceTarget =
  | { kind: "citation"; citation: Citation }
  | { kind: "page"; documentVersionId: string; documentTitle: string; page: number };

// #35 — the source page a citation points at, so verification is one click, not a promise.
// Opens at the cited page; prev/next let the clinician read the surrounding context, which
// is the whole reason a page reference beats a paraphrase.
export function PdfViewer({
  target,
  onClose,
}: {
  target: SourceTarget;
  onClose: () => void;
}) {
  const t = useT();
  const documentVersionId =
    target.kind === "citation" ? target.citation.document_version_id : target.documentVersionId;
  const documentTitle =
    target.kind === "citation" ? target.citation.document_title : target.documentTitle;
  const initialPage = target.kind === "citation" ? target.citation.page_start : target.page;

  // page and error initialise from the target; the parent gives this component a `key`
  // per target, so selecting a different one remounts it fresh at the new page
  // rather than syncing through an effect.
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(initialPage);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState<number>(0);
  const containerRef = useRef<HTMLDivElement>(null);

  // Fit the page to the panel, and reflow when it resizes.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);

  const fileUrl = `/api/pdf/${documentVersionId}`;

  // Highlight the cited quote on the page so verification is a glance, not a re-read. The
  // PDF text layer is split into fragments that do not line up with the quote's boundaries,
  // so match by word overlap rather than substring: a fragment most of whose significant
  // words are in the quote is part of the quoted passage. Imperfect where the quote's words
  // recur elsewhere on the page — a highlight is an aid to find the text, not a claim about
  // it; the quote shown on the left is the verbatim record.
  const quoteWords = useMemo(() => {
    if (target.kind !== "citation") return new Set<string>();
    const words = target.citation.quote.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    return new Set(words.filter((w) => w.length >= 3));
  }, [target]);

  const highlightQuote = useCallback(
    (item: { str: string }) => {
      const escaped = item.str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      const words = (item.str.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter(
        (w) => w.length >= 3,
      );
      if (words.length === 0) return escaped;
      const overlap = words.filter((w) => quoteWords.has(w)).length / words.length;
      // Inline style, not a CSS class: react-pdf sanitises the returned HTML but keeps the
      // style attribute, and this avoids depending on a stylesheet selector matching the
      // text layer's markup. color:transparent because the visible glyphs are the canvas
      // underneath; the mark only contributes the highlight background.
      return overlap >= 0.6
        ? `<mark style="background-color:rgba(250,204,21,0.5);color:transparent;border-radius:2px">${escaped}</mark>`
        : escaped;
    },
    [quoteWords],
  );

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium">{documentTitle}</p>
          <p className="text-xs text-neutral-500">
            {target.kind === "citation"
              ? `${t.pdf.cited}: ${pageDisplay(target.citation)}`
              : `${t.pdf.unreadablePage}: ${target.page}`}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded px-2 py-1 text-xs text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          aria-label="Close source viewer"
        >
          {t.pdf.close}
        </button>
      </header>

      {/* page controls */}
      <div className="flex items-center justify-center gap-3 border-b border-neutral-200 px-3 py-1.5 text-xs dark:border-neutral-800">
        <button
          type="button"
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
          className="rounded px-2 py-0.5 disabled:opacity-30 hover:bg-neutral-100 dark:hover:bg-neutral-800"
        >
          {t.pdf.prev}
        </button>
        <span className="tabular-nums text-neutral-500">
          {t.pdf.page} {page}
          {numPages ? ` ${t.pdf.of} ${numPages}` : ""}
        </span>
        <button
          type="button"
          onClick={() => setPage((p) => (numPages ? Math.min(numPages, p + 1) : p + 1))}
          disabled={numPages !== null && page >= numPages}
          className="rounded px-2 py-0.5 disabled:opacity-30 hover:bg-neutral-100 dark:hover:bg-neutral-800"
        >
          {t.pdf.next}
        </button>
      </div>

      <div ref={containerRef} className="flex-1 overflow-auto bg-neutral-100 p-3 dark:bg-neutral-900">
        {error ? (
          <p className="p-4 text-sm text-red-700 dark:text-red-300">{error}</p>
        ) : (
          <Document
            file={fileUrl}
            onLoadSuccess={({ numPages }) => setNumPages(numPages)}
            onLoadError={(e) =>
              setError(t.pdf.error(e.message))
            }
            loading={<p className="p-4 text-sm text-neutral-500">{t.pdf.loading}</p>}
          >
            {width > 0 && (
              <Page
                pageNumber={page}
                width={width - 24}
                renderTextLayer
                renderAnnotationLayer
                // No renderer at all on an unreadable page, rather than one that highlights
                // nothing: there is no quote to find there, and running the matcher over a
                // page opened precisely because its text did not extract would be looking
                // for something this system already said it could not read.
                customTextRenderer={target.kind === "citation" ? highlightQuote : undefined}
              />
            )}
          </Document>
        )}
      </div>
    </div>
  );
}
