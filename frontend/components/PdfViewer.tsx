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

// #35 — the source page a citation points at, so verification is one click, not a promise.
// Opens at the cited page; prev/next let the clinician read the surrounding context, which
// is the whole reason a page reference beats a paraphrase.
export function PdfViewer({
  citation,
  onClose,
}: {
  citation: Citation;
  onClose: () => void;
}) {
  const t = useT();
  // page and error initialise from the citation; the parent gives this component a `key`
  // per citation, so selecting a different one remounts it fresh at the new cited page
  // rather than syncing through an effect.
  const [numPages, setNumPages] = useState<number | null>(null);
  const [page, setPage] = useState(citation.page_start);
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

  const fileUrl = `/api/pdf/${citation.document_version_id}`;

  // Highlight the cited quote on the page so verification is a glance, not a re-read. The
  // PDF text layer is split into fragments that do not line up with the quote's boundaries,
  // so match by word overlap rather than substring: a fragment most of whose significant
  // words are in the quote is part of the quoted passage. Imperfect where the quote's words
  // recur elsewhere on the page — a highlight is an aid to find the text, not a claim about
  // it; the quote shown on the left is the verbatim record.
  const quoteWords = useMemo(() => {
    const words = citation.quote.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    return new Set(words.filter((w) => w.length >= 3));
  }, [citation.quote]);

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
          <p className="truncate text-xs font-medium">{citation.document_title}</p>
          <p className="text-xs text-neutral-500">{t.pdf.cited}: {pageDisplay(citation)}</p>
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
                customTextRenderer={highlightQuote}
              />
            )}
          </Document>
        )}
      </div>
    </div>
  );
}
