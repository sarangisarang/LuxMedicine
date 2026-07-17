import type { Citation } from "./api";

// "p. 45" or "pp. 45-46" — matches Citation.page_display on the backend.
export function pageDisplay(c: Citation): string {
  return c.page_start === c.page_end
    ? `p. ${c.page_start}`
    : `pp. ${c.page_start}-${c.page_end}`;
}
