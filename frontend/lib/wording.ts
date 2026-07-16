import type { Citation, NoAnswerReason } from "./api";

// The human wording for an empty answer. The backend deliberately returns only an enum
// (NoAnswerReason) and no prose — "the wording a clinician reads lives in the UI, where a
// human chose it" (app/schemas/answer.py). The three are kept distinct on purpose: "the
// corpus has nothing" is a fact about the guidelines; "verification failed" is us
// malfunctioning, and reporting our own failure as an absence of guidance would be the
// quietest lie the system could tell.
export const NO_ANSWER_WORDING: Record<NoAnswerReason, string> = {
  no_relevant_sources: "No guideline in the corpus covers this question.",
  sources_do_not_answer:
    "Guidelines were found, but none of their passages answer this question.",
  verification_failed:
    "An answer was produced but could not be verified against its source, so it is being withheld. This is a system fault, not an absence of guidance.",
};

// "p. 45" or "pp. 45-46" — matches Citation.page_display on the backend.
export function pageDisplay(c: Citation): string {
  return c.page_start === c.page_end
    ? `p. ${c.page_start}`
    : `pp. ${c.page_start}-${c.page_end}`;
}
