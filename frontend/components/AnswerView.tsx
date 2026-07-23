"use client";

import type { AnswerPayload, Citation } from "@/lib/api";

import { CombinedText } from "./CombinedText";
import { useT } from "./LanguageContext";
import { SourceGroupCard } from "./SourceGroupCard";

// The whole answer. Its one job is to render what the backend returned without editorialising
// — no summary line, no "best" source, no reordering. The two honest signals a naive UI
// would drop live here at the top level:
//   - an empty answer states WHY (the enum → human wording), never a blank panel;
//   - rejected quotes are counted, never silently dropped, because a clinician seeing four
//     quotes cannot tell a fifth was discarded.
export function AnswerView({
  answer,
  onOpenSource,
}: {
  answer: AnswerPayload;
  onOpenSource?: (c: Citation) => void;
}) {
  const t = useT();

  if (!answer.groups || answer.groups.length === 0) {
    // Rephrasing helps only when the corpus did not answer — not on our own faults
    // (verification_failed, table_not_citable), where a differently-worded question changes
    // nothing and the hint would be false comfort.
    const reason = answer.no_answer_reason;
    const canRephrase =
      reason === "sources_do_not_answer" || reason === "no_relevant_sources";
    return (
      <section className="mt-8 rounded-lg border border-neutral-300 p-4 dark:border-neutral-700">
        <p className="text-sm text-neutral-700 dark:text-neutral-300">
          {reason ? t.noAnswer[reason] : t.noAnswer.fallback}
        </p>
        {canRephrase && (
          <p className="mt-2 text-xs text-neutral-500">{t.noAnswer.hint}</p>
        )}
        {/* "The guideline does not say this" and "we could not read the page where it says
            it" arrive as the same sentence otherwise — and clinically they are opposite: one
            closes the question, the other means look elsewhere. The answered path has said
            this per source since #41; the DECLINE path said nothing, which is the half that
            mattered. NHLBI EPR-3 is the only asthma document and 116 of its 440 pages hold no
            chunk at all, so every asthma decline was made against a quarter-missing source,
            silently. */}
        {Object.keys(answer.incomplete_sources ?? {}).length > 0 && (
          <p
            role="status"
            className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
          >
            {t.noAnswer.incompleteSources(
              Object.entries(answer.incomplete_sources ?? {}).map(
                ([title, pages]) => `${title} (${pages})`,
              ),
            )}
          </p>
        )}
      </section>
    );
  }

  return (
    <section className="mt-8 space-y-6">
      {(answer.rejected_citations ?? 0) > 0 && (
        <p
          role="status"
          className="rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
        >
          {t.rejected(answer.rejected_citations ?? 0)}
        </p>
      )}

      {answer.groups.map((group, i) => (
        <SourceGroupCard key={i} group={group} onOpenSource={onOpenSource} />
      ))}

      {/* All quotes gathered into one readable block — the same verbatim text, never a
          summary. Rendered last so the per-source cards, with their translations and staleness
          banners, remain the primary view. */}
      <CombinedText answer={answer} />
    </section>
  );
}
