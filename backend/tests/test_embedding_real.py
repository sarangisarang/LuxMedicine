"""The real multilingual-e5-large, exercised end to end (#6, #10).

Skipped unless the `embeddings` extra is installed, so CI does not pull several GB it
has no use for. Which means: **this is the only place the shipped embedder is actually
run.** Everything else uses a fake. Run it locally before trusting a corpus to it.

What it pins is #6's premise. The dimension is a one-way door — `vector(1024)` is in
migration 0001 and changing it means re-embedding everything — and multilingual is the
whole reason for choosing this model over a stronger English-only one. Both were
decisions made from documentation. These assert them against the actual weights.
"""

import pytest

pytest.importorskip("sentence_transformers", reason="needs the 'embeddings' extra")

from app.core.config import get_settings  # noqa: E402
from app.services.embedding import DEFAULT_MODEL, E5Embedder  # noqa: E402

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def embedder() -> E5Embedder:
    return E5Embedder(DEFAULT_MODEL, batch_size=8)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_the_model_produces_the_dimension_the_schema_was_built_for(embedder):
    """#6, verified against the weights rather than the model card."""
    assert embedder.dimension == get_settings().embedding_dim == 1024


def test_vectors_are_normalised(embedder):
    """The HNSW index is vector_cosine_ops. Unnormalised vectors would still insert and
    still return results — just subtly worse ones, with nothing raising."""
    [vector] = embedder.embed_passages(["The target dose of enalapril is 20 mg twice daily."])
    assert _cosine(vector, vector) == pytest.approx(1.0, abs=1e-3)


def test_a_query_matches_its_passage_better_than_an_unrelated_one(embedder):
    """The minimum retrieval has to do before #14 can be built on it."""
    passages = embedder.embed_passages(
        [
            "The target dose of enalapril is 20 mg twice daily in heart failure.",
            "Screening for colorectal cancer should begin at age 45.",
        ]
    )
    query = embedder.embed_query("What dose of enalapril should I use?")

    assert _cosine(query, passages[0]) > _cosine(query, passages[1])


def test_the_prefixes_are_not_cosmetic(embedder):
    """E5's prefixes are load-bearing, and this is the evidence for the interface
    refusing to let a caller skip them."""
    text = "The target dose of enalapril is 20 mg twice daily in heart failure."
    query = "What dose of enalapril should I use?"

    with_prefix = _cosine(embedder.embed_query(query), embedder.embed_passages([text])[0])
    without = _cosine(embedder._encode([query])[0], embedder._encode([text])[0])

    assert with_prefix != pytest.approx(without, abs=1e-4), (
        "prefixes made no difference — check the model is really multilingual-e5-*"
    )


@pytest.mark.parametrize(
    "language,question",
    [
        ("Georgian", "რა დოზით უნდა დავნიშნო ენალაპრილი?"),
        ("German", "Welche Dosis Enalapril soll ich verwenden?"),
        ("Russian", "Какую дозу эналаприла назначить?"),
        ("French", "Quelle dose d'énalapril dois-je utiliser ?"),
    ],
)
def test_a_non_english_query_finds_an_english_passage(embedder, language, question):
    """#16's foundation, and the entire reason #6 chose this model.

    A clinician asking in Georgian must reach an English guideline: the corpus is
    English, the users are not. If this fails, the multilingual claim is wrong and the
    model choice needs revisiting *before* a corpus is embedded with it.
    """
    passages = embedder.embed_passages(
        [
            "The target dose of enalapril is 20 mg twice daily in heart failure.",
            "Screening for colorectal cancer should begin at age 45.",
        ]
    )
    query = embedder.embed_query(question)

    relevant = _cosine(query, passages[0])
    unrelated = _cosine(query, passages[1])
    assert relevant > unrelated, f"{language} query did not reach the matching English passage"


def test_the_same_meaning_in_two_languages_lands_close_together(embedder):
    """Cross-lingual alignment, stated directly: "hypertension" and "ჰიპერტენზია" must
    be nearer each other than either is to an unrelated clinical term."""
    hypertension_en, hypertension_ka, unrelated = embedder.embed_passages(
        ["hypertension", "ჰიპერტენზია", "bone fracture of the femur"]
    )

    assert _cosine(hypertension_en, hypertension_ka) > _cosine(hypertension_en, unrelated)
