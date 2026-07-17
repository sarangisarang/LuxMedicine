"use client";

import type { AnswerPayload, Citation } from "@/lib/api";

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
    return (
      <section className="mt-8 rounded-lg border border-neutral-300 p-4 dark:border-neutral-700">
        <p className="text-sm text-neutral-700 dark:text-neutral-300">
          {answer.no_answer_reason
            ? t.noAnswer[answer.no_answer_reason]
            : t.noAnswer.fallback}
        </p>
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
    </section>
  );
}
