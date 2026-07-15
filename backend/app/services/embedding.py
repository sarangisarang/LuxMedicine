"""Embedding, with E5's asymmetric prefixes made unforgettable (#10).

multilingual-e5-large is trained with two prefixes: `passage: ` for indexed text and
`query: ` for searches. They are not decoration — dropping them costs real retrieval
quality, and mixing them up (indexing with `query: `, searching with `passage: `) costs
more.

The dangerous part is that getting it wrong **raises nothing**. The vectors are the
right shape, the insert succeeds, the search returns results — just worse ones. Nobody
finds out until a clinician does not get the guideline they uploaded, and by then the
whole corpus needs re-embedding.

So there is no generic `embed()` on this interface. There is `embed_passages()` and
`embed_query()`, and the prefix is applied inside. A caller cannot forget what it is
never asked to supply.

The model choice is #6: 1024 dimensions, self-hostable, so clinical text is embedded on
our own infrastructure inside the EU rather than posted to a third-party API.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.config import get_settings

DEFAULT_MODEL = "intfloat/multilingual-e5-large"

PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "


class EmbeddingDimensionError(Exception):
    """The model does not produce the dimension the schema was built for.

    Raised eagerly at construction rather than at insert time: `vector(1024)` is pinned
    in migration 0001, so a model that disagrees cannot be used without re-embedding
    every chunk. Better to fail on startup than halfway through a corpus.
    """

    def __init__(self, model: str, produced: int, expected: int) -> None:
        super().__init__(
            f"{model} produces {produced}-dim vectors, but the schema is vector({expected}). "
            "Changing the model means a new migration and re-embedding every chunk (#6)."
        )


@runtime_checkable
class Embedder(Protocol):
    """Deliberately asymmetric: E5 needs different prefixes for passages and queries,
    so the interface offers no way to embed text without saying which it is."""

    @property
    def dimension(self) -> int: ...

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class E5Embedder:
    """multilingual-e5-large via sentence-transformers, run locally.

    sentence-transformers and torch are an optional extra (`pip install -e ".[embeddings]"`)
    and imported lazily, so the API, the tests, and CI do not carry a multi-gigabyte
    dependency they never use.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        batch_size: int = 16,
        device: str | None = None,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover — depends on the extra
            raise ImportError(
                "E5Embedder needs the 'embeddings' extra: pip install -e \".[embeddings]\""
            ) from exc

        self._model = SentenceTransformer(model_name, device=device)
        self._batch_size = batch_size
        self._model_name = model_name

        # Renamed in sentence-transformers 5.x; the old name still works but warns.
        # Support both rather than pin a floor — the model is the contract here, not the
        # library version.
        get_dimension = getattr(
            self._model, "get_embedding_dimension", None
        ) or self._model.get_sentence_embedding_dimension

        produced = get_dimension()
        expected = get_settings().embedding_dim
        if produced != expected:
            raise EmbeddingDimensionError(model_name, produced, expected)

        self._dimension = produced

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _encode(self, prefixed: list[str]) -> list[list[float]]:
        # normalize_embeddings=True is required, not tuning: the HNSW index is built on
        # vector_cosine_ops, and normalised vectors make cosine distance well-behaved.
        # batch_size keeps a long guideline from being encoded one chunk at a time (slow)
        # or all at once (out of memory on a modest GPU).
        vectors = self._model.encode(
            prefixed,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode([PASSAGE_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([QUERY_PREFIX + text])[0]
