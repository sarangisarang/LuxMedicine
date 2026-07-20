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


class E5OnnxEmbedder:
    """The same multilingual-e5-large, run as an int8 ONNX graph via onnxruntime.

    Same model, same 1024 dimensions, same `passage:`/`query:` prefixes, same mean-pool +
    L2-normalise as E5Embedder — so the vectors live in the same space and the corpus does not
    have to be re-embedded to switch runtimes (parity is measured, not assumed). What changes is
    the footprint: this path needs neither torch nor sentence-transformers, so the process RSS
    drops from ~2.3 GB to a few hundred MB — the difference between fitting on a small shared box
    and not. onnxruntime + a tokenizer are the only heavy imports, both lazy.

    The pooling is replicated by hand because ORTModelForFeatureExtraction returns token vectors,
    not a sentence embedding; getting it wrong is the silent-quality failure the module docstring
    warns about, which is why test parity against the fp32 model is part of shipping this.
    """

    def __init__(
        self,
        model_dir,
        *,
        batch_size: int = 16,
    ) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover — depends on the extra
            raise ImportError(
                "E5OnnxEmbedder needs the 'onnx' extra: pip install -e \".[onnx]\""
            ) from exc

        from pathlib import Path

        model_dir = Path(model_dir)
        try:
            model_file = next(model_dir.glob("*quantized*.onnx"))
        except StopIteration as exc:
            raise FileNotFoundError(
                f"no *quantized*.onnx under {model_dir} — build it with the conversion step"
            ) from exc

        self._np = np
        self._session = ort.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._session.get_inputs()}
        self._tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self._batch_size = batch_size
        self._model_name = f"{DEFAULT_MODEL} (onnx-int8)"

        # The graph's output width IS the schema contract, exactly as for the fp32 path.
        probe = self._encode(["passage: dimension probe"])[0]
        produced = len(probe)
        expected = get_settings().embedding_dim
        if produced != expected:
            raise EmbeddingDimensionError(self._model_name, produced, expected)
        self._dimension = produced

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _encode(self, prefixed: list[str]) -> list[list[float]]:
        np = self._np
        out: list[list[float]] = []
        for start in range(0, len(prefixed), self._batch_size):
            batch = prefixed[start : start + self._batch_size]
            enc = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="np",
            )
            feed = {
                "input_ids": enc["input_ids"].astype(np.int64),
                "attention_mask": enc["attention_mask"].astype(np.int64),
            }
            # e5 is XLM-RoBERTa based; some exports still declare token_type_ids (all zeros).
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros_like(enc["input_ids"], dtype=np.int64)

            last_hidden = self._session.run(None, feed)[0]  # (batch, seq, dim)
            mask = enc["attention_mask"][..., None].astype(np.float32)
            summed = (last_hidden * mask).sum(axis=1)
            counts = np.clip(mask.sum(axis=1), 1e-9, None)
            mean = summed / counts  # mean pooling over non-pad tokens
            norm = mean / np.clip(
                np.linalg.norm(mean, axis=1, keepdims=True), 1e-12, None
            )  # L2 normalise, as normalize_embeddings=True does
            out.extend(vector.tolist() for vector in norm.astype(np.float32))
        return out

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode([PASSAGE_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([QUERY_PREFIX + text])[0]


def make_embedder() -> Embedder:
    """Build the embedder the settings ask for — the one place the backend is chosen.

    Both backends satisfy the Embedder Protocol and produce interchangeable vectors; the choice is
    purely footprint (see Settings.embedding_backend). Kept here so main.py and every CLI wire the
    same one instead of hard-coding E5Embedder.
    """
    settings = get_settings()
    if settings.embedding_backend == "onnx":
        if settings.embedding_onnx_dir is None:
            raise ValueError(
                "embedding_backend=onnx requires embedding_onnx_dir to point at the "
                "quantized model directory"
            )
        return E5OnnxEmbedder(settings.embedding_onnx_dir)
    return E5Embedder()
