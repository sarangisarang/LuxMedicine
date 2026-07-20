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

**Data residency, and why it is asserted rather than configured.** #6 self-hosts the
embedding model *specifically* so clinical text stays in the EU, and #30 self-hosts
Keycloak because sending a clinician's identity to a US processor while keeping their
question in the EU would be a contradiction. This call sends the question *and* the
guideline passages, which is more sensitive than the identity that decision turned on.

Two ways to reach Gemini, and only one can keep that promise. Both measured:

- **The Developer API** (an API key). The resolved base URL is
  `generativelanguage.googleapis.com` — no regional pinning exists on this path at all.
- **Vertex AI** with a project and a *regional* location. The resolved base URL becomes
  `europe-west3-aiplatform.googleapis.com`, and Google commits to ML processing staying
  in the EU for regional endpoints. The *global* Vertex endpoint does not: its own
  documentation says you "can't control or know which region your ML processing requests
  are sent to".

**So `endpoint` reads the URL the SDK actually resolved, not the region that was asked
for.** google-gemini/gemini-cli#27984 (open, filed June 2026) is precisely that gap: the
JavaScript SDK silently drops the location when an API key is present, routes to the
global endpoint, and the config keeps displaying the region — a green dashboard over data
in another jurisdiction. The Python SDK refuses that combination outright (measured:
`ValueError: Project/location and API key are mutually exclusive`), which is the right
behaviour and is not a reason to trust a config instead of a URL.

Same shape as every other bug this project has turned up: HTTP 200 on a broken chain, a
mutation harness reporting SURVIVED for a mutant that would not compile, a verifier
reading its own cache, a superuser silently skipping every RLS policy. A mechanism that
looks like success when it is absent.
"""

from __future__ import annotations

import os

from app.core.config import get_settings
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
# **The free tier's real constraint is requests per day, not tokens, and it is not uniform
# across models.** Measured 2026-07-17 from the AI Studio rate-limit dashboard, because the
# 429 body only ever names the model it just refused:
#
#     gemini-3-flash-preview / 2.5-flash / 3.5-flash    20 RPD    <- the default, and a wall
#     gemini-3.1-flash-lite                            500 RPD    15 RPM, 250K TPM
#     gemma-4-26b / gemma-4-31b                     14,400 RPD    30 RPM, 16K TPM
#
# Twenty a day cannot survive one debugging session, let alone a 500-question rejection_rate
# run; 500 a day can do both. The default is NOT changed to the lite model on that basis
# alone: it answered one real question with zero #19 rejections, and one question is not a
# faithfulness measurement. Which model belongs here is a decision for the rejection_rate
# run — which the 500 RPD finally makes possible. Set GEMINI_MODEL to choose; see
# .env.example.
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

# Vertex regional endpoints whose ML processing Google places in the EU. An explicit list,
# not a `"europe" in url` test: a substring check passes for any host that happens to
# contain the word, and a typo'd region would sail through it. A region missing from this
# list does not mean it is not European — it means nobody checked, and for this purpose
# those are the same answer.
EU_PROCESSING_HOSTS = (
    "europe-west1-aiplatform.googleapis.com",
    "europe-west3-aiplatform.googleapis.com",
    "europe-west4-aiplatform.googleapis.com",
    "europe-west8-aiplatform.googleapis.com",
    "europe-west9-aiplatform.googleapis.com",
    "europe-north1-aiplatform.googleapis.com",
    "europe-central2-aiplatform.googleapis.com",
    "europe-southwest1-aiplatform.googleapis.com",
)


class ProcessingLeavesTheEU(RuntimeError):
    """The resolved endpoint is not one that keeps ML processing in the EU."""


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
        vertex_project: str | None = None,
        vertex_location: str | None = None,
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover — depends on the extra
            raise ImportError(
                'GeminiExtractor needs the "gemini" extra: pip install -e ".[gemini]". '
                "Note the package is google-genai, not the retired google-generativeai."
            ) from exc

        project = vertex_project or os.environ.get("GOOGLE_CLOUD_PROJECT") or None
        location = vertex_location or os.environ.get("GOOGLE_CLOUD_LOCATION") or None

        if project:
            # Vertex, with a region. Needs real credentials — ADC or a service account.
            # An API key is not accepted on this path and the SDK says so rather than
            # quietly going global, which is the failure #27984 describes in the JS SDK.
            self._client = genai.Client(vertexai=True, project=project, location=location)
        else:
            # The Developer API. Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the
            # environment — never a constructor argument, because a key passed as a
            # parameter is a key that ends up in a traceback, a log line, or a fixture.
            # There is no regional pinning on this path; see the module docstring.
            self._client = genai.Client()
        self._genai = genai
        self.model = model or DEFAULT_MODEL
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout_ms = timeout_ms
        self._attempts = attempts

    @property
    def endpoint(self) -> str:
        """The base URL the SDK actually resolved — not the region that was requested.

        The entire point. #27984 is a config that says europe-west3 and a client that
        talks to the global endpoint; reading the URL back is the only way to know which
        of the two you have.
        """
        return self._client._api_client._http_options.base_url or ""

    @property
    def processes_in_eu(self) -> bool:
        host = self.endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")
        return host in EU_PROCESSING_HOSTS

    def refuse_unless_eu_processing(self) -> None:
        """Raise unless the resolved endpoint keeps ML processing in the EU.

        Called from `extract()` outside local development — see there. Not from
        `__init__`: constructing an extractor to *measure* it against invented fixtures
        is legitimate, and refusing at construction would mean the only way to test the
        thing is to already be compliant.
        """
        if not self.processes_in_eu:
            raise ProcessingLeavesTheEU(
                f"resolved endpoint is {self.endpoint!r}, which is not a Vertex regional "
                "endpoint that keeps ML processing in the EU. #6 self-hosts the embedding "
                "model so clinical text stays in the EU; sending the question and the "
                "guideline passages elsewhere contradicts it. Set GOOGLE_CLOUD_PROJECT "
                "and GOOGLE_CLOUD_LOCATION to a European region, with real credentials — "
                "an API key routes to the global endpoint, where Google's own "
                "documentation says you cannot know where processing happens."
            )

    def extract(self, question: str, passages: list[str]) -> ExtractionResult | None:
        # The boundary where clinical text leaves this process, and therefore the only
        # place the residency promise can actually be kept.
        #
        # An available method nobody calls is the failure #29 was about: a chain nobody
        # walks proves nothing, and a refusal nobody invokes refuses nothing. So it is
        # invoked here rather than left for a deployment to remember — the same reason
        # the audit lives inside the pipeline instead of in the handler, where a second
        # endpoint added later could forget it.
        #
        # Gated on `environment` for the same reason `settings` gates the OIDC check:
        # local development must be able to measure this against invented fixtures
        # without a GCP project, and a deployment must not be able to send a clinician's
        # question anywhere it likes.
        #
        # `allow_non_eu_inference` is the single, explicit, off-by-default demo escape hatch (see
        # Settings). It relaxes ONLY residency, only when deliberately set, and main.py announces it
        # loudly at boot — the opposite of a silent bypass. Everything else (auth, audit, cache
        # rule) stays enforced. For real clinical use it stays off and Vertex-EU carries inference.
        settings = get_settings()
        if settings.environment != "local" and not settings.allow_non_eu_inference:
            self.refuse_unless_eu_processing()

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
