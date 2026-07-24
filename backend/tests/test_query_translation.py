"""Query translation before retrieval — the Georgian-retrieval fix (measured 2026-07-24).

The point under test is the DECISION: translate to the corpus language when, and only when, the
query is not already in it — and never let a translation outage turn into a failed query. The
retrieval improvement itself was measured on prod (ka raw missed CDC MEC p124, ka->en put it back
at rank 1); this pins the routing around it so a later edit cannot quietly stop translating
Georgian, or start translating a query that was already English.
"""

from __future__ import annotations

import pytest

from app.core.vocabulary import Sector
from app.services.query_translation import corpus_language_for, translate_for_retrieval
from app.services.translation import TranslationRateLimited, TranslationUnavailable

KA = "მიგრენის აურის დროს რომელი კონტრაცეპტული მეთოდია უსაფრთხო?"


class _Result:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeTranslator:
    def __init__(self, out: str = "EN", raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._out = out
        self._raises = raises

    def translate(self, quote: str, *, target_language: str) -> _Result:
        self.calls.append((quote, target_language))
        if self._raises is not None:
            raise self._raises
        return _Result(self._out)


def test_corpus_language_per_sector():
    assert corpus_language_for(Sector.MEDICAL) == "English"
    assert corpus_language_for(Sector.LEGAL) == "German"


def test_english_medical_is_not_translated():
    tr = FakeTranslator()
    q, src = translate_for_retrieval(
        "Which methods are safe?", sector=Sector.MEDICAL, language="English", translator=tr
    )
    assert (q, src) == ("Which methods are safe?", None)
    assert tr.calls == [], "the translator must not be called when already in the corpus language"


def test_georgian_medical_is_translated_to_english():
    tr = FakeTranslator(out="Which methods are safe?")
    q, src = translate_for_retrieval(KA, sector=Sector.MEDICAL, language="Georgian", translator=tr)
    assert (q, src) == ("Which methods are safe?", "Georgian")
    assert tr.calls == [(KA, "English")]


def test_georgian_script_triggers_even_without_a_language_hint():
    """The measured-broken case must not depend on the hint being present or correct."""
    tr = FakeTranslator(out="translated")
    q, src = translate_for_retrieval(KA, sector=Sector.MEDICAL, language=None, translator=tr)
    assert q == "translated" and src == "auto"
    assert tr.calls[0][1] == "English"


def test_german_legal_is_not_translated():
    tr = FakeTranslator()
    q, src = translate_for_retrieval(
        "Welche Leistungsphasen?", sector=Sector.LEGAL, language="German", translator=tr
    )
    assert (q, src) == ("Welche Leistungsphasen?", None)
    assert tr.calls == []


def test_english_legal_is_translated_to_german():
    tr = FakeTranslator(out="Welche Leistungsphasen umfasst die Objektplanung?")
    q, src = translate_for_retrieval(
        "Which service phases does object planning cover?",
        sector=Sector.LEGAL,
        language="English",
        translator=tr,
    )
    assert q == "Welche Leistungsphasen umfasst die Objektplanung?" and src == "English"
    assert tr.calls == [("Which service phases does object planning cover?", "German")]


@pytest.mark.parametrize("exc", [TranslationRateLimited("quota"), TranslationUnavailable("down")])
def test_translation_failure_falls_back_to_the_raw_query(exc):
    """A translation outage must degrade retrieval, not fail the request — and it must NOT be
    reported as translated, so the fallback is never mistaken for a good translation."""
    tr = FakeTranslator(raises=exc)
    q, src = translate_for_retrieval(KA, sector=Sector.MEDICAL, language="Georgian", translator=tr)
    assert (q, src) == (KA, None)


def test_an_empty_translation_falls_back_to_the_raw_query():
    tr = FakeTranslator(out="   ")
    q, src = translate_for_retrieval(KA, sector=Sector.MEDICAL, language="Georgian", translator=tr)
    assert (q, src) == (KA, None)


def test_no_translator_returns_the_raw_query():
    q, src = translate_for_retrieval(KA, sector=Sector.MEDICAL, language="Georgian", translator=None)
    assert (q, src) == (KA, None)
