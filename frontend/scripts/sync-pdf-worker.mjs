// Copy pdf.js's worker into public/ so it is served from our own origin, not a CDN.
//
// Self-hosted on purpose: the same data-residency and no-third-party stance the backend
// takes (self-hosted embeddings, EU-only processing) should not be undone by the browser
// fetching a worker from unpkg. Copying from the installed pdfjs-dist guarantees the worker
// version matches react-pdf's bundled pdfjs exactly — a mismatch is the classic "the API
// version does not match the Worker version" crash. Runs on predev/prebuild.

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const source = join(root, "node_modules", "pdfjs-dist", "build", "pdf.worker.min.mjs");
const dest = join(root, "public", "pdf.worker.min.mjs");

mkdirSync(dirname(dest), { recursive: true });
copyFileSync(source, dest);
console.log(`synced pdf worker -> public/pdf.worker.min.mjs`);
