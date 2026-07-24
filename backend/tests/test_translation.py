"""Translating one quote, and the ways it must fail.

The translation is the one piece of model-written clinical text this system emits, so most of
what matters here is what it does NOT do: it is not part of an answer, it never substitutes
the original when it fails, and a spent quota is not an internal error.
"""


import pytest
from httpx import ASGITransport, AsyncClient

from app.services.translation import (
    MachineTranslation,
    TranslationRateLimited,
    TranslationUnavailable,
)

QUOTE = "Do not routinely advise people with heart failure to restrict their sodium."


class FakeTranslator:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises

    def translate(self, quote: str, *, target_language: str) -> MachineTranslation:
        if self._raises:
            raise self._raises
        return MachineTranslation(
            source_quote=quote, target_language=target_language, text="übersetzt"
        )


async def _post(translator, body: dict) -> tuple[int, dict]:
    from app.api.translation import get_translator
    from app.core.auth import Clinician, current_clinician
    from app.main import app

    app.dependency_overrides[get_translator] = lambda: translator
    app.dependency_overrides[current_clinician] = lambda: Clinician(
        actor_id="dr-001", clinic_id="clinic-demo"
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/translate", json=body)
            return r.status_code, r.json()
    finally:
        app.dependency_overrides.clear()


async def test_a_translation_says_what_it_is():
    """The response cannot be rendered without a field announcing it is machine output — and
    the field is `text`, never `quote`: `Citation.quote` means "verbatim and validated"
    everywhere else in this system, and a translation is neither."""
    status, body = await _post(FakeTranslator(), {"quote": QUOTE, "target_language": "German"})

    assert status == 200
    assert body["text"] == "übersetzt"
    assert body["is_machine_translation"] is True
    assert body["is_verified"] is False
    assert "quote" not in {k for k in body if k == "quote"}
    assert body["source_quote"] == QUOTE


async def test_a_spent_quota_is_429_not_500():
    """The regression this test exists for: a clinician clicked Übersetzen and got "Internal
    Server Error" because google-genai's 429 propagated uncaught. The free tier is 20
    requests/day per model — running out is a fact about a budget, and reporting it as an
    internal error sends someone looking for a bug that is not there."""
    status, body = await _post(
        FakeTranslator(raises=TranslationRateLimited("quota is exhausted")),
        {"quote": QUOTE, "target_language": "German"},
    )

    assert status == 429, "a spent quota must not read as a fault in the system"
    assert "quota" in str(body).lower()


async def test_a_failed_translation_is_reported_not_papered_over():
    """Never the original text with a German label on it: showing English and calling it
    German is a quieter lie than showing nothing."""
    status, body = await _post(
        FakeTranslator(raises=TranslationUnavailable("the model returned nothing")),
        {"quote": QUOTE, "target_language": "German"},
    )

    assert status == 502
    assert QUOTE not in str(body), "the original must not be returned dressed as a translation"


async def test_rate_limited_is_a_kind_of_unavailable():
    """So a caller that only knows the general failure still fails closed."""
    assert issubclass(TranslationRateLimited, TranslationUnavailable)


@pytest.mark.parametrize("body", [{"quote": "", "target_language": "German"}, {"quote": QUOTE}])
async def test_an_incomplete_request_is_refused(body):
    status, _ = await _post(FakeTranslator(), body)
    assert status == 422
