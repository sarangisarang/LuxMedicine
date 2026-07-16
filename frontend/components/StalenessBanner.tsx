import type { SourceGroup } from "@/lib/api";

// #36 — a superseded edition must say so before its text is trusted. Rendered from #17's
// is_superseded / superseding_version_label, resolved to the current edition's label. Shown,
// never used to hide the quote: an old guideline is still what it said, and a clinician may
// have reason to read it — the banner informs, it does not censor.
export function StalenessBanner({ group }: { group: SourceGroup }) {
  if (!group.is_superseded) return null;
  return (
    <p
      role="status"
      className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
    >
      This edition has been superseded
      {group.superseding_version_label
        ? ` by ${group.superseding_version_label}.`
        : "."}
    </p>
  );
}
