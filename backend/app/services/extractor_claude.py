"""The Claude adapter for #18.

**Unproven.** Nothing here has been run — it needs an API key and it spends money, and
neither is mine to decide. Everything else in this project was measured before it was
trusted; this is the exception, and it is the exception on purpose. The embedder set the
precedent (tests/test_embedding_real.py) — run this against the real API before trusting
a clinician's question to it.

Optional extra (`pip install -e ".[llm]"`), imported lazily, exactly like the embedder.
The `Extractor` protocol is what the rest of the system depends on; this file is one
implementation of it and can be replaced without touching anything else.

**Data residency is unresolved and blocks nothing here.** #6 chose a self-hosted
embedding model so clinical text stays in the EU. The same question applies to this call
and the answer is not obvious: `inference_geo` is a first-party request parameter (Opus
4.6+, not available on Bedrock or Vertex) that pins where inference runs, and
`usage.inference_geo` reports where it actually ran — which is the right lever. The
accepted values are not documented in what I have, so the parameter is exposed here as
an unset option rather than guessed at. Confirm the value before any real deployment;
until then this runs wherever Anthropic routes it.
"""

from __future__ import annotations

from app.core.vocabulary import Sector
from app.services.answering import ExtractionResult

DEFAULT_MODEL = "claude-opus-4-8"

# Non-streaming default. Selections are short — a dozen quotes is well under this — and
# streaming would buy nothing for a call whose entire output is consumed at once.
DEFAULT_MAX_TOKENS = 16000

SYSTEM_PROMPT = """\
You are the extraction step of a clinical search engine. You do not advise, diagnose, \
recommend, or summarise. You select spans of text.

You will be given a clinician's question and numbered passages from clinical guidelines. \
Return the spans of those passages that answer the question, each with the number of the \
passage it came from.

Rules, in order of importance:

1. Every quote must be copied character-for-character from the passage you cite. Do not \
paraphrase, do not correct spelling or grammar, do not normalise units, do not join \
distant spans with an ellipsis. A quote that is not present verbatim in its passage is \
discarded, so an inexact quote is a lost answer, not a helpful approximation.

2. Two separate spans are two entries, even from the same passage. A span is contiguous.

3. Quote only what answers the question. If a passage qualifies a dose with a condition, \
the qualification is part of the answer — include it in the same span rather than \
quoting the dose alone.

4. If none of the passages answer the question, return no quotes. An empty answer is \
correct and expected. A quote from a passage that does not answer the question is worse \
than no answer, because the clinician cannot tell the difference.

5. Never write anything that is not a quote. You have no field for commentary and no \
reason to want one.\
"""

# The legal corpus gets its own wording, and SYSTEM_PROMPT above is left byte-identical.
#
# That is not tidiness, it is the only way to keep #38's measurement worth anything. Four
# wordings of the clinical prompt were run over the full 26-question set and each one
# bought a case and sold another while the aggregate sat still; the note above records
# which. Editing that text to make it domain-neutral — "guidelines" to "sources",
# "clinician" to "user" — would invalidate every one of those runs, and the damage would
# be invisible, because the aggregate is exactly what does not move.
#
# So this is a copy with the domain nouns changed and the five rules identical in order
# and force. The rules are about extraction, not about medicine; what changes is what the
# model is told it is reading.
#
# Rule 3's example changes with it. In the clinical prompt it is a dose qualified by a
# condition; here it is a fee qualified by an Honorarzone, because that is the same
# failure in this corpus — a §-paragraph quoted without the Absatz that conditions it
# reads as unconditional law, and a verbatim quote is exactly how it would arrive.
LEGAL_SYSTEM_PROMPT = """\
You are the extraction step of a legal search engine over German statutes and \
ordinances. You do not advise, interpret, apply the law to a situation, or summarise. \
You select spans of text.

You will be given a question and numbered passages from German legal texts. Return the \
spans of those passages that answer the question, each with the number of the passage it \
came from.

Rules, in order of importance:

1. Every quote must be copied character-for-character from the passage you cite. Do not \
paraphrase, do not correct spelling or grammar, do not modernise wording, do not join \
distant spans with an ellipsis. A quote that is not present verbatim in its passage is \
discarded, so an inexact quote is a lost answer, not a helpful approximation.

2. Two separate spans are two entries, even from the same passage. A span is contiguous.

3. Quote only what answers the question. If a passage conditions a rule — on an \
Honorarzone, a threshold value, a Leistungsphase, an exception in a following Absatz — \
that condition is part of the answer. Include it in the same span rather than quoting \
the bare rule, which would read as unconditional.

4. If none of the passages answer the question, return no quotes. An empty answer is \
correct and expected. A quote from a passage that does not answer the question is worse \
than no answer, because the reader cannot tell the difference.

5. Never write anything that is not a quote. You have no field for commentary and no \
reason to want one.\
"""


def system_prompt_for(sector: Sector) -> str:
    """The prompt for this corpus.

    A lookup rather than an if-chain so that a sector added later without a prompt fails
    loudly at the KeyError instead of silently being handed the clinical wording and
    asked to read statutes as if they were guidelines.
    """
    return _PROMPTS[sector]


_PROMPTS: dict[Sector, str] = {
    Sector.MEDICAL: SYSTEM_PROMPT,
    Sector.LEGAL: LEGAL_SYSTEM_PROMPT,
}

# **There is no rule here about junk input, and that is a measured decision (#38).**
#
# Three questions the corpus handles differently sit on one axis, and a rule added here moves
# them together rather than separately:
#
#   junk-topic-only / junk-meta    "heart failure", "What can you do?" — no question is asked
#   contraception-in-ckd           a real question whose answer lives in another speciality's
#                                  guideline — the case that decided against a speciality filter
#   not-covered-diabetes-hba1c     a real question, topically adjacent, genuinely absent
#
# Measured against the full 26-question set, one run per wording (2026-07-17):
#
#   BASE            junk 6/8   ckd answered      hba1c declined
#   v2 "passages that share the input's vocabulary are not an answer to it"
#                   junk 8/8   ckd DECLINED      hba1c declined
#   v3 same, narrowed to "only for deciding whether a question was asked"
#                   junk 6/8   ckd answered      hba1c declined     — identical to BASE
#   v4 "decide from the input alone, before the passages are in front of you"
#                   junk 6/8   ckd answered      hba1c ANSWERED     — worse than BASE
#
# v2's sentence was doing two jobs at once. It is what makes the model decline junk, and it is
# also the only thing telling it that a passage sharing the question's vocabulary is not
# thereby an answer — so it took contraception-in-ckd down with the junk, and removing it in v4
# made the model *more* willing to answer the adjacent-but-absent question from the nearest
# passage. Every wording bought one and sold another.
#
# The three cases look alike from here: this step sees a question and some passages that
# mention its words, and cannot tell "no question was asked" from "the question is real and the
# answer is genuinely elsewhere". So junk does not belong in this prompt. It belongs before
# retrieval, where the input is judged on its own and the failure mode is mild — a
# misclassified question asks the clinician to rephrase, where a wrong decline here silently
# withholds an answer that exists. That is the trade the aggregate hid: answered stayed 15/18
# across all four runs while three questions swapped underneath it.


def render_prompt(question: str, passages: list[str]) -> str:
    """The user turn: the question, then the numbered passages.

    The numbering is the only handle the model has on a passage — see answering.py on
    why it is an integer rather than the chunk's UUID.
    """
    numbered = "\n\n".join(f"[{i}]\n{text}" for i, text in enumerate(passages, start=1))
    return f"Question:\n{question}\n\nPassages:\n\n{numbered}"


class ClaudeExtractor:
    """Selects passages and spans via the Claude API.

    Requires the `llm` extra. See the module docstring: this has not been run.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = "high",
        inference_geo: str | None = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover — depends on the extra
            raise ImportError(
                'ClaudeExtractor needs the "llm" extra: pip install -e ".[llm]"'
            ) from exc

        self._client = anthropic.Anthropic()
        self._anthropic = anthropic
        self.model = model
        self._max_tokens = max_tokens
        self._effort = effort

        # Pins where inference runs. Left unset by default because the accepted values
        # are not established here and a wrong guess would silently route clinical text
        # somewhere it must not go — see the module docstring.
        self._inference_geo = inference_geo

    def extract(
        self, question: str, passages: list[str], sector: Sector = Sector.MEDICAL
    ) -> ExtractionResult | None:
        if not passages:
            return ExtractionResult(quotes=[])

        request: dict = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": system_prompt_for(sector),
            # Adaptive must be set explicitly on Opus 4.8 — omitting the field runs
            # without thinking at all.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort},
            "messages": [{"role": "user", "content": render_prompt(question, passages)}],
        }
        if self._inference_geo is not None:
            request["inference_geo"] = self._inference_geo

        response = self._client.messages.parse(output_format=ExtractionResult, **request)

        if response.stop_reason == "refusal":
            # A clinical question can trip a safety classifier, and on a refusal the
            # output need not match the schema. Returning None routes this to
            # SOURCES_DO_NOT_ANSWER rather than crashing — a clinician sees "these
            # passages do not answer", which is true, instead of a 500.
            return None

        return response.parsed_output
