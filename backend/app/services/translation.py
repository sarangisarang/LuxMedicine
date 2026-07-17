"""Machine translation of one verbatim quote — an aid for reading, never evidence.

**This is the one place model-written clinical text can leave the system, and it is fenced
off deliberately.** Everything else obeys `schemas/answer.py`: the only clinical text in an
answer is `Citation.quote`, a verbatim span validated character-for-character against its
chunk (#19). A translation cannot be validated that way and never will be — it is by
definition not a verbatim span of the source.

So the fence:

- A translation is **not part of `AnswerPayload`** and never enters the audit trail as the
  answer. The evidence a clinic hands a lawyer stays the guideline's own words.
- It is **requested explicitly**, per quote, by a clinician who already has the original in
  front of them — not produced alongside every answer.
- The response type is called `MachineTranslation` and its field is `text`, not `quote`, so
  nothing downstream can mistake it for a validated span.

**What can go wrong, stated plainly.** From this corpus: *"Do not routinely advise people
with heart failure to restrict their sodium or fluid consumption."* Drop the "not" in
translation and the sentence says the opposite, next to a real page citation, with nothing
to catch it. That is why the rules below lead with negation, and why the UI must say this is
machine output rather than the guideline speaking.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

# Ordered by what hurts most when it goes wrong, not by how a style guide would rank them.
SYSTEM_PROMPT = """You translate a single sentence from a clinical guideline into the target \
language. You are not a clinician. You do not explain, summarise, simplify, or improve the \
text.

Rules, in order of importance:

1. Preserve negation exactly. "do not", "should not", "avoid", "unless", "except" — dropping \
or inverting a negation writes the opposite clinical instruction, which is the worst thing \
you can do here.
2. Preserve every number, dose, unit, threshold, age and frequency exactly as written. Do not \
convert units. Do not round.
3. Preserve the strength of the recommendation. "suggest" is weaker than "recommend"; "may" \
is weaker than "should"; "consider" is weaker than "offer". Never upgrade or soften.
4. Add nothing: no explanation, no context, no clarification, no bracketed notes, no hedging \
of your own.
5. Omit nothing, including citations, evidence grades and parenthetical labels.
6. If a clinical term has no established equivalent in the target language, keep the original \
term rather than inventing one.

Return only the translated sentence, with no preamble and no commentary."""


@dataclass(frozen=True)
class MachineTranslation:
    """A translation of one quote. Machine output — not the guideline's words.

    `text`, not `quote`: `Citation.quote` means "verbatim and validated" everywhere else in
    this system, and this is neither.
    """

    source_quote: str
    target_language: str
    text: str


class Translator:
    """What the API depends on. A protocol in spirit — see GeminiTranslator."""

    def translate(self, quote: str, *, target_language: str) -> MachineTranslation:  # pragma: no cover
        raise NotImplementedError


class GeminiTranslator(Translator):
    """Same client and the same regional caveat as the extractor — see extractor_gemini."""

    def __init__(self, model: str | None = None, *, temperature: float = 0.0) -> None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover — depends on the extra
            raise ImportError(
                'GeminiTranslator needs the "gemini" extra: pip install -e ".[gemini]"'
            ) from exc

        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or None
        location = os.environ.get("GOOGLE_CLOUD_LOCATION") or None
        if project:
            self._client = genai.Client(vertexai=True, project=project, location=location)
        else:
            self._client = genai.Client()
        self.model = model or DEFAULT_MODEL
        self._temperature = temperature

    def translate(self, quote: str, *, target_language: str) -> MachineTranslation:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.model,
            contents=f"Target language: {target_language}\n\nSentence:\n{quote}",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                # Zero, for the same reason the extractor uses zero: this is a transformation
                # of a given sentence, not a place for the model to have ideas.
                temperature=self._temperature,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise TranslationUnavailable("the model returned nothing")
        return MachineTranslation(
            source_quote=quote, target_language=target_language, text=text
        )


class TranslationUnavailable(RuntimeError):
    """The translation could not be produced. Reported, never substituted with the original:
    showing the English and calling it German would be a quieter lie than showing nothing."""
