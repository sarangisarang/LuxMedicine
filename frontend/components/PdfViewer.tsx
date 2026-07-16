"use client";

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import type { Citation } from "@/lib/api";
import { pageDisplay } from "@/lib/wording";

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

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium">{citation.document_title}</p>
          <p className="text-xs text-neutral-500">cited: {pageDisplay(citation)}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded px-2 py-1 text-xs text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          aria-label="Close source viewer"
        >
          Close
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
          ‹ Prev
        </button>
        <span className="tabular-nums text-neutral-500">
          Page {page}
          {numPages ? ` of ${numPages}` : ""}
        </span>
        <button
          type="button"
          onClick={() => setPage((p) => (numPages ? Math.min(numPages, p + 1) : p + 1))}
          disabled={numPages !== null && page >= numPages}
          className="rounded px-2 py-0.5 disabled:opacity-30 hover:bg-neutral-100 dark:hover:bg-neutral-800"
        >
          Next ›
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
              setError(
                `Could not load the source PDF: ${e.message}. Is the backend running?`,
              )
            }
            loading={<p className="p-4 text-sm text-neutral-500">Loading source…</p>}
          >
            {width > 0 && (
              <Page
                pageNumber={page}
                width={width - 24}
                renderTextLayer
                renderAnnotationLayer
              />
            )}
          </Document>
        )}
      </div>
    </div>
  );
}
