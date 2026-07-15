"""The Gemini adapter for #18.

One implementation of the `Extractor` protocol, alongside `extractor_claude.py`. The
protocol is what the rest of the system depends on; neither adapter is load-bearing, and
that was the point of putting a protocol there.

**The prompt is imported, not copied.** `SYSTEM_PROMPT` and `render_prompt` come from
`extractor_claude` — an unfortunate module name for a shared thing, and better than the
alternative: two adapters with drifting prompts cannot be compared, and the first
question anyone asks is "which model quotes more faithfully". That question needs the
inputs held constant.

**Temperature 0, which Claude cannot do.** The Anthropic adapter runs adaptive thinking,
and extended thinking pins temperature at 1. Gemini exposes it, and this is extraction:
there is no creative latitude to want. Copying a span is not a task with a distribution
worth sampling from.

**Data residency is unresolved, and here it is louder than it was for Claude.** #6
self-hosts the embedding model *specifically* so clinical text stays in the EU, and #30
self-hosts Keycloak because sending a clinician's identity to a US processor while
keeping their question in the EU would be a contradiction. This call sends the question
*and* the guideline passages — more sensitive than the identity that decision turned on.
The Gemini Developer API does not offer the regional pinning that would resolve it;
Vertex AI does, and moving there is a client change rather than a rewrite. Until that is
decided, this is fit for measurement against invented fixtures and not for a clinician's
question.
"""

from __future__ import annotations

import os

from app.services.answering import ExtractionResult
from app.services.extractor_claude import SYSTEM_PROMPT, render_prompt

# Gemini 1.5 is gone from the model list and 2.0 Flash is shut down. 3.5 Flash is the
# current stable flagship and is *not* the default here, because the key this was
# measured with cannot reach it: probing every model the key lists returned 504 for
# 3.5-flash and 429 for most of the rest. Four answered — 3-flash-preview (6.9s),
# 2.5-flash (0.7s), 3.1-flash-lite, 3.1-flash-lite-preview.
#
# So the default is the most capable model that actually responds, and the tier of the
# key decides what that is. Overridable by GEMINI_MODEL: a measurement taken against a
# model the deployment cannot reach is not a measurement of the deployment.
#
# Pinned rather than aliased: an extractor whose behaviour changes under it without a
# commit is an extractor whose measurements expire silently.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

# The first run took eleven minutes and reported five failures that were all one 503
# ("this model is currently experiencing high demand") multiplied by the SDK's retry
# loop. Reading that as "the model cannot quote verbatim" would have been a confident
# wrong conclusion of exactly the kind this project keeps catching — so the retries are
# bounded and the timeout is explicit. An extractor that hangs is an extractor whose
# failure mode is indistinguishable from a slow one.
DEFAULT_TIMEOUT_MS = 60_000
DEFAULT_ATTEMPTS = 2


class GeminiExtractor:
    """Selects passages and spans via the Gemini API.

    Requires the `gemini` extra: pip install -e ".[gemini]"
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 16000,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        attempts: int = DEFAULT_ATTEMPTS,
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover — depends on the extra
            raise ImportError(
                'GeminiExtractor needs the "gemini" extra: pip install -e ".[gemini]". '
                "Note the package is google-genai, not the retired google-generativeai."
            ) from exc

        # Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment. Never a
        # constructor argument: a key passed as a parameter is a key that ends up in a
        # traceback, a log line, or a test fixture.
        self._client = genai.Client()
        self._genai = genai
        self.model = model or DEFAULT_MODEL
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout_ms = timeout_ms
        self._attempts = attempts

    def extract(self, question: str, passages: list[str]) -> ExtractionResult | None:
        if not passages:
            return ExtractionResult(quotes=[])

        from google.genai import types

        response = self._client.models.generate_content(
            model=self.model,
            contents=render_prompt(question, passages),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                http_options=types.HttpOptions(
                    timeout=self._timeout_ms,
                    retry_options={"attempts": self._attempts},
                ),
                # The schema is the guarantee, not the prompt. ExtractionResult has no
                # field for prose, so the model cannot return advice even if it wants to
                # — it would not parse. Same move as schemas/answer.py.
                response_mime_type="application/json",
                response_schema=ExtractionResult,
            ),
        )

        parsed = response.parsed
        if parsed is None:
            # A blocked or empty response. Routed to "these passages do not answer",
            # which is true, rather than crashing — the same choice the Claude adapter
            # makes on a refusal.
            return None

        # `.parsed` is already an ExtractionResult when response_schema is a Pydantic
        # model; validate anyway rather than trust the SDK's word for it.
        return ExtractionResult.model_validate(
            parsed if isinstance(parsed, dict) else parsed.model_dump()
        )
