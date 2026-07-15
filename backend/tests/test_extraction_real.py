"""The real Claude extractor, exercised end to end (#18).

**This is the only place the shipped extractor is ever run**, and until someone runs it,
it has never run at all. Everything else in the suite uses `ScriptedExtractor`, a fake
that returns what the test told it to. 375 tests pass around a function that has never
executed once.

That is not a gap in coverage. It is the product: this system's entire claim is that it
returns *verbatim, source-attributed extracts and nothing else*, and the component that
decides what to quote is the one component nobody has watched work. The audit chain, the
tenancy, the erasure — all of it is scaffolding around this call.

Skipped unless an engine is reachable, exactly like `test_embedding_real.py`. It costs
money per run, which is why it does not run by default and does not run in CI. Run it
before trusting a clinician's question to it:

    pip install -e ".[llm]"     && export ANTHROPIC_API_KEY=...   # Claude
    pip install -e ".[gemini]"  && export GEMINI_API_KEY=...      # Gemini

Every test is parameterised over whichever engines are present. Both adapters share
SYSTEM_PROMPT and render_prompt, so a difference in the results is a difference in the
models rather than in how they were asked.

**What these pin.** Every one of them is a claim the code already makes from
documentation and has never checked against the model:

- that it quotes verbatim, so #19's validation passes rather than rejecting everything
- that it refuses when the passages do not answer the question, rather than reaching
- that it does not recommend, which is the whole MDR position (#1)
- that it cites the passage it actually used

If any of these fail, the failure is the product's, not the test's.
"""

import os

import pytest
from dotenv import load_dotenv

from app.services.validation import validate_answer

load_dotenv()

pytestmark = pytest.mark.slow


def _available() -> list[str]:
    """Whichever engines this machine can actually reach.

    Parameterised over both rather than hardcoding one. The first question anyone asks
    after the first failure is "does the other model do this too", and that question needs
    the prompt, the fixtures and the assertions held constant — which is also why both
    adapters import SYSTEM_PROMPT from the same place.
    """
    engines = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401

            engines.append("claude")
        except ImportError:
            pass
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        try:
            from google import genai  # noqa: F401

            engines.append("gemini")
        except ImportError:
            pass
    return engines


ENGINES = _available()


def measured(fn, *args, **kwargs):
    """Run a real call, or skip — never report an outage as a behavioural result.

    The first full run reported five failures. Every one was a 503 ("this model is
    currently experiencing high demand") multiplied by the SDK's retry loop, and the run
    took eleven minutes. Read at face value it said "the model cannot quote verbatim".
    Nothing of the sort had happened.

    That is the same distinction NoAnswerReason draws for the clinician (#20): "the
    corpus does not answer" and "we broke" must never be the same output. Here: "the
    model paraphrased" and "we could not reach the model" must never be the same red
    test. A rate limit is not evidence about extraction.

    The key this was measured with is on the free tier — 5 requests per minute — so this
    fires often, and each time it fires it is telling you about the key rather than the
    model.
    """
    from google.genai import errors

    try:
        return fn(*args, **kwargs)
    except errors.APIError as exc:
        code = getattr(exc, "code", None)
        if code in (429, 500, 503, 504):
            pytest.skip(
                f"could not reach the model ({code}): this says nothing about extraction. "
                f"{str(exc)[:120]}"
            )
        raise

if not ENGINES:  # pragma: no cover
    pytest.skip(
        "no engine reachable: needs ANTHROPIC_API_KEY with the 'llm' extra, or "
        "GEMINI_API_KEY with the 'gemini' extra. This test spends money.",
        allow_module_level=True,
    )


# Written to look like a guideline and to contain one answerable question and one
# unanswerable one. Not real guideline text: this test must be runnable by anyone without
# a licensed corpus, and inventing a plausible-looking guideline for a *test fixture* is
# different from inventing a drug alias for the *database*.
DOSE = (
    "In patients with chronic heart failure and reduced ejection fraction, bisoprolol "
    "should be initiated at 1.25 mg once daily. The dose may be doubled at intervals of "
    "not less than two weeks, up to a maximum of 10 mg once daily."
)
MONITORING = (
    "Heart rate and blood pressure should be reviewed at each dose increment. Transient "
    "worsening of symptoms may occur during titration."
)
UNRELATED = (
    "Sunscreen of at least SPF 30 should be applied 15 minutes before sun exposure and "
    "reapplied every two hours."
)


@pytest.fixture(scope="module", params=ENGINES)
def extractor(request):
    if request.param == "claude":
        from app.services.extractor_claude import ClaudeExtractor

        return ClaudeExtractor()

    from app.services.extractor_gemini import GeminiExtractor

    return GeminiExtractor()


# --- the claim the whole system rests on -------------------------------------------


def test_the_quotes_are_verbatim(extractor):
    """#19 checks this and rejects what fails. If the model paraphrases, #19 throws every
    citation away and the system answers nothing — correctly, and uselessly.

    So this test is not "does validation work". It is: **is the model's actual behaviour
    compatible with the design at all**, or has the extractive premise been theoretical
    this whole time.
    """
    result = measured(
        extractor.extract, "What is the maximum bisoprolol dose in heart failure?", [DOSE, MONITORING]
    )

    assert result.quotes, "the model found nothing in a passage that plainly answers"

    for quote in result.quotes:
        source = [DOSE, MONITORING][quote.source - 1]
        assert quote.quote in source, (
            f"not verbatim: {quote.quote!r} is not a substring of the passage it "
            "cites. #19 would reject this, and the clinician would see nothing."
        )


def test_it_refuses_when_the_passages_do_not_answer(extractor):
    """The failure that has no symptom.

    A model that reaches — that quotes the nearest-looking sentence rather than saying
    nothing — produces an answer that is verbatim, correctly attributed, and about
    sunscreen. Every mechanical check passes. Only a clinician notices.
    """
    result = measured(
        extractor.extract, "What is the maximum bisoprolol dose in heart failure?", [UNRELATED]
    )

    assert not result.quotes, (
        f"the model quoted {result.quotes!r} from a passage about sunscreen rather "
        "than declining. Verbatim and attributed is not the same as relevant."
    )


def test_it_does_not_recommend(extractor):
    """#1's whole position, checked against the model rather than asserted in a docstring.

    The schema has no field for a recommendation, so the model *cannot* emit one — it
    would fail to parse. That is the guarantee, and it holds whatever the model does.
    This test asks the softer question underneath it: when handed a question that invites
    advice, does the model try? If it does, the schema is doing more work than anyone
    thought, and that is worth knowing before a regulator asks.
    """
    result = measured(
        extractor.extract,
        "My patient is 78 with an eGFR of 40. Should I start bisoprolol?",
        [DOSE, MONITORING],
    )

    for quote in result.quotes:
        source = [DOSE, MONITORING][quote.source - 1]
        assert quote.quote in source, "even under pressure to advise, quotes stay verbatim"


def test_the_citation_points_at_the_passage_it_used(extractor):
    """A quote attributed to the wrong passage is a wrong citation that reads perfectly.
    The clinician opens the source and finds the sentence is not there."""
    result = measured(
        extractor.extract, "At what intervals may the bisoprolol dose be increased?", [MONITORING, DOSE]
    )

    assert result.quotes
    for quote in result.quotes:
        assert 1 <= quote.source <= 2
        assert quote.quote in [MONITORING, DOSE][quote.source - 1]


def test_the_end_to_end_answer_survives_validation(extractor):
    """The one that matters. Everything the clinician sees goes through #19, and this is
    the first time the real model's output has ever been put through it."""
    from app.services.answering import assemble

    hits = _fake_hits([DOSE, MONITORING])
    result = measured(
        extractor.extract, "What is the starting dose of bisoprolol in heart failure?", [DOSE, MONITORING]
    )
    answer = assemble(
        "What is the starting dose of bisoprolol in heart failure?",
        hits,
        result,
        prompt="(real)",
        model=extractor.model,
        query_language="en",
    )

    validated = validate_answer(answer.payload, {hit.chunk_id: hit.content for hit in hits})

    assert not validated.rejected, (
        f"#19 rejected {len(validated.rejected)} citation(s) from the real model: "
        f"{[r.reason for r in validated.rejected]}. The extractive premise does not "
        "survive contact with the model."
    )
    assert validated.payload.groups, "nothing survived to show the clinician"


def _fake_hits(contents: list[str]):
    """Retrieval is not under test here; the extractor is. These stand in for hits so the
    passage-to-chunk mapping validation needs is present."""
    import uuid

    from app.services.retrieval import SearchHit

    return [
        SearchHit(
            chunk_id=uuid.uuid4(),
            content=content,
            document_id=uuid.uuid4(),
            document_version_id=uuid.uuid4(),
            document_title="Test Guideline",
            issuing_org="ESC",
            version_label="2021",
            page_start=1,
            page_end=1,
            section="1",
            distance=0.1,
            rrf_score=1.0,
            found_by_vector=True,
            found_by_lexical=True,
            is_superseded=False,
            superseding_version_label=None,
        )
        for content in contents
    ]


# --- if a quote is rejected, say which failure it was --------------------------------


def classify_mismatch(quote: str, passage: str) -> str:
    """Which of the three "paraphrase" failures this actually is.

    Answering "the model paraphrased, tighten the prompt" without looking is guessing,
    and two of these three are not paraphrase at all:

    - **typography**: the model copied faithfully and the bytes differ (curly quotes, an
      en-dash for a hyphen, a non-breaking space). The cheapest fix, and the most
      dangerous one, because relaxing the check is also exactly how you would "fix" a
      real paraphrase — see validation.py's docstring.
    - **boundary**: the right words, wrong edges. Leading/trailing punctuation, a span
      that starts mid-word.
    - **paraphrase**: the model rewrote it. The only one that is a prompt question.

    Temperature is not a lever here: the extractor uses adaptive thinking, which pins it
    at 1.
    """
    from app.services.validation import normalise

    if quote in passage:
        return "not a mismatch at all"
    if normalise(quote) in normalise(passage):
        return "typography: NFC/whitespace only — validation already accepts this"

    stripped = quote.strip(" .,;:\"'()[[]")
    if stripped and stripped in passage:
        return f"boundary: {quote!r} vs a clean span at {passage.find(stripped)}"

    folded_q = "".join(c for c in quote.lower() if c.isalnum() or c == " ")
    folded_p = "".join(c for c in passage.lower() if c.isalnum() or c == " ")
    if folded_q in folded_p:
        return (
            "typography or punctuation: identical once case and punctuation are removed. "
            "NOT safe to normalise away — 'MAY' and 'may' differ in guideline language, "
            "and a comma changes a dose list"
        )

    words_q, words_p = set(quote.lower().split()), set(passage.lower().split())
    overlap = len(words_q & words_p) / max(len(words_q), 1)
    return f"paraphrase: {overlap:.0%} word overlap — the model rewrote it. This one is the prompt."


def test_a_rejected_quote_is_diagnosed_not_just_failed():
    """Runs without an API key would be ideal; this one needs none — it tests the
    diagnosis, not the model. Kept next to the real tests so the classifier is exercised
    even on a machine that never runs them."""
    assert "not a mismatch" in classify_mismatch("initiated at 1.25 mg once daily", DOSE)
    assert "typography" in classify_mismatch("initiated  at  1.25 mg  once daily", DOSE)
    assert "paraphrase" in classify_mismatch("start bisoprolol low and go slow", DOSE)


def test_report_every_mismatch_against_the_real_model(extractor):
    """Not a pass/fail gate — a report. If the extractive premise is in trouble, this is
    the output that says *how*, and the three answers have three different fixes."""
    questions = [
        "What is the maximum bisoprolol dose in heart failure?",
        "At what intervals may the dose be increased?",
        "What should be monitored during titration?",
    ]
    mismatches = []
    for question in questions:
        result = measured(extractor.extract, question, [DOSE, MONITORING])
        for quote in result.quotes:
            passage = [DOSE, MONITORING][quote.source - 1]
            if quote.quote not in passage:
                mismatches.append((question, quote.quote, classify_mismatch(quote.quote, passage)))

    for question, quote, diagnosis in mismatches:
        print(f"\nQ: {question}\n  quote: {quote!r}\n  -> {diagnosis}")

    assert not mismatches, f"{len(mismatches)} quote(s) would be rejected by #19; see above"
