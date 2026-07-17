"""Translate one quote (#32 language, read side).

**Separate from `/queries` on purpose.** An answer is verbatim spans and provenance, and the
audit row records exactly that — see `schemas/answer.py`. If a translation were a field on
`AnswerPayload` it would ride into the trail as though the system had said it, and #19 could
never validate it. So it lives here: a clinician who already has the original in front of
them asks for a reading aid, explicitly, one quote at a time.

Authenticated like everything else — this spends money per call and the caller has to be
someone.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.auth import Clinician, current_clinician
from app.services.translation import (
    TranslationRateLimited,
    TranslationUnavailable,
    Translator,
)

router = APIRouter(prefix="/translate", tags=["translate"])


class TranslateRequest(BaseModel):
    # The quote as it was shown. Not a chunk id: the client translates what the clinician is
    # looking at, and sending the text back makes that explicit rather than re-deriving it.
    quote: str = Field(min_length=1, max_length=4000)
    target_language: str = Field(min_length=2, max_length=16)


class TranslateResponse(BaseModel):
    """Machine output, and the field names say so.

    `text`, never `quote`: `Citation.quote` means "verbatim and validated" everywhere else in
    this system. `is_machine_translation` is always true and exists so a UI cannot render this
    without a field telling it what it is.
    """

    source_quote: str
    target_language: str
    text: str
    is_machine_translation: bool = True
    is_verified: bool = False


def get_translator() -> Translator:  # pragma: no cover - overridden in tests and at startup
    raise NotImplementedError(
        "no translator is wired up: install the `gemini` extra and override this"
    )


@router.post("", response_model=TranslateResponse)
async def post_translation(
    request: TranslateRequest,
    clinician: Clinician = Depends(current_clinician),
    translator: Translator = Depends(get_translator),
) -> TranslateResponse:
    """A reading aid for one quote. Not an answer, not evidence, not audited as either."""
    try:
        result = translator.translate(request.quote, target_language=request.target_language)
    except TranslationRateLimited as exc:
        # 429, not 500: the quota being spent is a fact about a budget, and answering with
        # "Internal Server Error" sends a clinician looking for a bug that is not there. The
        # free tier is 20 requests/day per model, so this is a condition they will meet.
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    except TranslationUnavailable as exc:
        # Reported, never papered over by returning the original: showing English and
        # labelling it German would be a quieter lie than showing nothing.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return TranslateResponse(
        source_quote=result.source_quote,
        target_language=result.target_language,
        text=result.text,
    )
