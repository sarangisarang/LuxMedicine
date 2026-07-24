"""Translate a query into its corpus's language before retrieval.

Measured 2026-07-24 on prod: a Georgian medical question did not retrieve the decisive CDC MEC
row (p124), and translating the same question to English put it back at rank 1. The cause was the
cross-lingual pair, not anything downstream — and structurally, `hybrid_search`'s lexical half is
`TEXT_SEARCH_CONFIG="english"`, so a non-English query gets no lexical signal at all and RRF loses
the precision the lexical half provides. So the fix is upstream of retrieval: ask the corpus in
its own language. German already retrieves (high-resource pair); Georgian was the broken case.

Only retrieval uses the translation. The extractor and the audit keep the ORIGINAL question: the
clinician asked in their language and the trail must say so, and the extractor is itself
multilingual. A translation failure degrades retrieval (falls back to the raw query) rather than
failing the request — a translation outage must not reach a clinician as "no answer".

The translation adds a new silent failure — a bad translation degrades retrieval invisibly — so
`translate_for_retrieval` returns what it translated *from*, for the caller to record. Otherwise
"the Georgian question found nothing" conflates two causes, weak retrieval and a bad translation,
the exact conflation `error` vs a corpus-silent decline (pipeline) and `incomplete_sources` vs a
real not-found (answering) already refuse.
"""

from __future__ import annotations

import logging

from app.core.vocabulary import Sector
from app.services.translation import (
    TranslationRateLimited,
    TranslationUnavailable,
    Translator,
)

logger = logging.getLogger(__name__)

# The language each sector's documents are actually written in — English clinical guidance (CDC),
# German law (Baurecht). The frontend sends the query `language` as these same strings ("English",
# "German", "Georgian"), which are also exactly what the translator's `target_language` expects.
_CORPUS_LANGUAGE = {Sector.MEDICAL: "English", Sector.LEGAL: "German"}


def corpus_language_for(sector: Sector) -> str:
    return _CORPUS_LANGUAGE[sector]


def _has_georgian(text: str) -> bool:
    """Georgian script (U+10A0–U+10FF) is unambiguous, so it triggers translation even when the
    language hint is absent or wrong — the one measured-broken case must not depend on the hint."""
    return any("Ⴀ" <= ch <= "ჿ" for ch in text)


def translate_for_retrieval(
    question: str,
    *,
    sector: Sector,
    language: str | None,
    translator: Translator | None,
) -> tuple[str, str | None]:
    """Return (query_to_retrieve_with, translated_from).

    `translated_from` is the declared source language when a translation actually happened, and
    None otherwise. When no translation is needed (already the corpus language) or possible (no
    translator, or the call failed), the original question comes back unchanged.
    """
    target = corpus_language_for(sector)
    declared_other = bool(language) and language.strip().casefold() != target.casefold()
    if not (declared_other or _has_georgian(question)):
        return question, None
    if translator is None:
        return question, None
    try:
        translated = translator.translate(question, target_language=target).text
    except (TranslationRateLimited, TranslationUnavailable) as exc:
        # Degrade, do not fail: retrieve on the raw query. A silent decline here is honest —
        # the extractor still runs — and this line is why it is not silent to an operator.
        logger.warning("query translation unavailable, retrieving on raw query: %s", exc)
        return question, None
    translated = (translated or "").strip()
    if not translated:
        return question, None
    logger.info("retrieval query translated for search: %s -> %s", language or "auto", target)
    return translated, (language or "auto")
