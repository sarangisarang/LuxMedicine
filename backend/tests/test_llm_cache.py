"""Memoising the model call (services/llm_cache.py).

Most of this file is about the two things the cache must NOT do: outlive a change to the
corpus, and exist anywhere but a developer's machine.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.vocabulary import Sector
from app.services.answering import ExtractionResult, SelectedQuote
from app.services.llm_cache import (
    CachingExtractor,
    CachingTranslator,
    DiskCache,
    cache_key,
)
from app.services.translation import MachineTranslation, TranslationRateLimited

PASSAGES = ["passage one", "passage two"]
QUESTION = "Should people with asthma avoid beta-blockers?"


class CountingExtractor:
    def __init__(self, result: ExtractionResult | None) -> None:
        self.calls = 0
        self.sectors: list[Sector] = []
        self._result = result

    def extract(
        self, question: str, passages: list[str], sector=Sector.MEDICAL
    ) -> ExtractionResult | None:
        self.calls += 1
        self.sectors.append(sector)
        return self._result


class CountingTranslator:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls = 0
        self._raises = raises

    def translate(self, quote: str, *, target_language: str) -> MachineTranslation:
        self.calls += 1
        if self._raises:
            raise self._raises
        return MachineTranslation(
            source_quote=quote, target_language=target_language, text="übersetzt"
        )


def _result() -> ExtractionResult:
    return ExtractionResult(quotes=[SelectedQuote(source=1, quote="passage one")])


# --- it saves the request -------------------------------------------------------------


def test_the_same_question_costs_one_request(tmp_path):
    inner = CountingExtractor(_result())
    extractor = CachingExtractor(inner, DiskCache(tmp_path), model="m")

    first = extractor.extract(QUESTION, PASSAGES)
    second = extractor.extract(QUESTION, PASSAGES)

    assert inner.calls == 1, "the second ask must not reach the model"
    assert second == first


def test_a_declining_model_is_remembered_as_a_decision(tmp_path):
    """None is a result — the model declining to answer — not an absent one. Inferring it
    from a missing file would ask again forever, which is the case that burns the quota."""
    inner = CountingExtractor(None)
    extractor = CachingExtractor(inner, DiskCache(tmp_path), model="m")

    assert extractor.extract(QUESTION, PASSAGES) is None
    assert extractor.extract(QUESTION, PASSAGES) is None
    assert inner.calls == 1


# --- it must not outlive what it was computed from ------------------------------------


def test_changed_passages_are_a_miss(tmp_path):
    """The property that makes this safe rather than a stale-answer machine: the key is the
    passages, so re-indexing a chunk or a different retrieval ranking cannot be served from
    a cache computed against the old corpus. No TTL to tune — the miss is structural."""
    inner = CountingExtractor(_result())
    extractor = CachingExtractor(inner, DiskCache(tmp_path), model="m")

    extractor.extract(QUESTION, PASSAGES)
    extractor.extract(QUESTION, ["passage one", "passage two REVISED"])

    assert inner.calls == 2, "a changed corpus must not be answered from the cache"


def test_a_different_model_is_a_miss(tmp_path):
    inner = CountingExtractor(_result())
    cache = DiskCache(tmp_path)

    CachingExtractor(inner, cache, model="gemini-3-flash-preview").extract(QUESTION, PASSAGES)
    CachingExtractor(inner, cache, model="gemini-2.5-flash").extract(QUESTION, PASSAGES)

    assert inner.calls == 2, "two models do not share a reply"


def test_the_key_cannot_collide_across_a_boundary():
    """("ab", "c") and ("a", "bc") must not be the same request."""
    assert cache_key("ab", "c") != cache_key("a", "bc")


# --- failures are not answers ---------------------------------------------------------


def test_a_spent_quota_is_not_cached(tmp_path):
    """Remembering "rate limited" would turn a quota that resets tomorrow into a permanent
    one."""
    inner = CountingTranslator(raises=TranslationRateLimited("quota spent"))
    translator = CachingTranslator(inner, DiskCache(tmp_path), model="m")

    for _ in range(2):
        with pytest.raises(TranslationRateLimited):
            translator.translate("q", target_language="German")

    assert inner.calls == 2, "a failure must be retried, not remembered"


def test_a_corrupt_entry_is_a_miss_not_an_error(tmp_path):
    """The worst a broken cache may do is cost one request."""
    cache = DiskCache(tmp_path)
    key = cache_key("x")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")

    assert cache.get(key) is None


def test_a_translation_is_reused(tmp_path):
    inner = CountingTranslator()
    translator = CachingTranslator(inner, DiskCache(tmp_path), model="m")

    translator.translate("q", target_language="German")
    again = translator.translate("q", target_language="German")

    assert inner.calls == 1
    assert again.text == "übersetzt"


def test_a_different_target_language_is_a_miss(tmp_path):
    inner = CountingTranslator()
    translator = CachingTranslator(inner, DiskCache(tmp_path), model="m")

    translator.translate("q", target_language="German")
    translator.translate("q", target_language="Georgian")

    assert inner.calls == 2


# --- and it must not exist outside a developer's machine -------------------------------


def test_a_cache_outside_local_refuses_to_boot():
    """The guard that matters, asserted rather than documented.

    The key is derived from the question and #28 exists to make a question unrecoverable —
    redact_query destroys the text and the salt and cannot reach a file named after its
    hash. And a frozen reply from a non-deterministic model falsifies rejection_rate. A flag
    would be the wrong shape for the same reason core/auth.py has no AUTH_DISABLED: it ships,
    and everything appears to work.
    """
    with pytest.raises(ValidationError) as exc:
        Settings(
            environment="production",
            oidc_issuer="https://issuer.example/realms/x",
            oidc_audience="luxmedicine-api",
            llm_cache_dir="/tmp/cache",
        )

    assert "LLM_CACHE_DIR" in str(exc.value)


def test_a_cache_is_allowed_locally():
    settings = Settings(environment="local", llm_cache_dir="/tmp/cache")
    assert settings.llm_cache_dir is not None
