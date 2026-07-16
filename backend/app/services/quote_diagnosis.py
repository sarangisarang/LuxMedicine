"""Why a quote failed #19 (#43).

#19 answers one question — is this quote in that chunk — and the answer is a boolean. A
boolean is enough to protect a clinician and useless for deciding what to do next, because
"not in the chunk" covers failures with nothing in common:

- **typography** — copied faithfully, bytes differ. Not a failure at all; #19 normalises
  NFC and whitespace runs and accepts these.
- **boundary** — right words, wrong edges.
- **mis-cited** — verbatim, in a *different* retrieved passage. The model quoted honestly
  and numbered it wrong. Nothing was invented.
- **paraphrase** — reworded the passage it cites. The prompt already says never to.
- **synthesis** — two real fragments from *different* passages stitched into a sentence
  that exists nowhere.
- **parametric leak** — the model answered from what it knows, not from what it was given.

The last two are the dangerous ones, and the first real rejection this project ever saw
was one of them. The model returned:

    "Rapid sustained decline in GFR could also be considered an indication for referral"

"indication for referral" is in the corpus — attached to *proteinuria*, on p82 and p83.
"Rapid sustained decline in GFR" is a real concept from elsewhere. The sentence is
neither. It is also, almost certainly, **clinically true** — which is what makes it the
worst case rather than the funniest one: a system that returns correct unsourced clinical
claims is not a search engine, it is an adviser, and that is the MDR line this project was
built not to cross. #19 caught it because it was unsourced, not because it was wrong.

**Calling that a paraphrase would send the next fix to the wrong place.** The prompt
already says "never paraphrase" and the model did not paraphrase — it assembled. This
module exists so the difference is visible before someone tunes the wrong knob.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.services.validation import normalise


class Verdict(str, Enum):
    CLEAN = "clean"
    TYPOGRAPHY = "typography"
    BOUNDARY = "boundary"
    MIS_CITED = "mis_cited"
    PARAPHRASE = "paraphrase"
    SYNTHESIS = "synthesis"
    PARAMETRIC_LEAK = "parametric_leak"


@dataclass(frozen=True)
class Diagnosis:
    verdict: Verdict
    detail: str

    @property
    def is_invention(self) -> bool:
        """Whether the model produced text the corpus does not contain.

        The distinction that matters for what to do next. Typography and boundary are
        ours to normalise; mis-cited is a numbering bug; paraphrase is a prompt problem.
        These two are the model reaching past its evidence.
        """
        return self.verdict in (Verdict.SYNTHESIS, Verdict.PARAMETRIC_LEAK)


# Content-bearing runs of words. Short enough to find in the corpus, long enough that
# hitting one is not a coincidence.
_NGRAM = 4

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _ngrams(words: list[str], n: int = _NGRAM) -> set[str]:
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def diagnose(
    quote: str,
    cited: str,
    *,
    other_passages: list[str] | None = None,
    corpus_contains: "callable[[str], bool] | None" = None,
) -> Diagnosis:
    """Why this quote is not in that chunk.

    `corpus_contains` answers "does any chunk in the whole corpus contain this phrase" —
    which is what separates a sentence assembled from real fragments (synthesis) from one
    the model brought with it (a parametric leak). Without it the two collapse, and they
    have different answers: synthesis is a prompt and chunking problem, a leak is a model
    problem.
    """
    if quote in cited:
        return Diagnosis(Verdict.CLEAN, "verbatim in the cited passage")

    if normalise(quote) in normalise(cited):
        return Diagnosis(
            Verdict.TYPOGRAPHY, "differs only in NFC/whitespace — #19 accepts this"
        )

    stripped = quote.strip(" .,;:\"'()[]“”‘’")
    if stripped and stripped in cited:
        return Diagnosis(Verdict.BOUNDARY, "right words, wrong edges")

    for i, passage in enumerate(other_passages or [], start=1):
        if normalise(quote) in normalise(passage):
            return Diagnosis(
                Verdict.MIS_CITED,
                f"verbatim in passage {i} — quoted honestly, numbered wrong",
            )

    quote_words = _words(quote)
    if len(quote_words) < _NGRAM:
        return Diagnosis(Verdict.PARAPHRASE, "too short to attribute")

    grams = _ngrams(quote_words)
    cited_grams = _ngrams(_words(cited))
    shared_with_cited = grams & cited_grams

    if corpus_contains is not None:
        # Phrases the corpus has, that the cited passage does not. If the sentence is
        # built from those, the model stitched real material from elsewhere.
        elsewhere = {g for g in grams - cited_grams if corpus_contains(g)}
        if elsewhere and not shared_with_cited:
            return Diagnosis(
                Verdict.SYNTHESIS,
                f"{len(elsewhere)}/{len(grams)} phrases exist in the corpus but not in the "
                f"cited passage — assembled from elsewhere, e.g. {sorted(elsewhere)[0]!r}",
            )
        if elsewhere and shared_with_cited:
            return Diagnosis(
                Verdict.SYNTHESIS,
                f"{len(shared_with_cited)}/{len(grams)} phrases from the cited passage, "
                f"{len(elsewhere)} from elsewhere — stitched",
            )
        if not elsewhere and not shared_with_cited:
            return Diagnosis(
                Verdict.PARAMETRIC_LEAK,
                "no phrase of this appears anywhere in the corpus — the model answered "
                "from what it knows, not from what it was given",
            )

    overlap = len(set(quote_words) & set(_words(cited))) / max(len(set(quote_words)), 1)
    return Diagnosis(
        Verdict.PARAPHRASE, f"{overlap:.0%} of its words are in the cited passage, reworded"
    )
