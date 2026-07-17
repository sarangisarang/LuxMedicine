"""Memoise the model call, so asking the same thing twice costs one request.

**Why this is safe where caching an *answer* would not be.** The cache sits around the
extractor and the translator — the model call — and nothing else. The pipeline still runs in
full on every request: retrieval runs, #19 validates the quotes against the live chunks, the
hash-chained audit row is written, row-level security applies. Only the model's reply is
remembered. `app/api/queries.py` still has no unaudited path, because there is no path around
it — this replaces a network call, not a request.

**It self-invalidates, which is the part that makes it correct.** The key is
`sha256(model + question + passages)`. If the corpus changes, or a chunk is re-indexed, or
retrieval ranks differently, the passages change and so does the key. A stale answer for a
changed corpus is not reachable — the miss is structural, not a TTL someone has to tune.

**Local development only, enforced at boot rather than documented.** Two reasons, both real:

1. *It would falsify the measurement.* The model is not deterministic even at temperature 0 —
   the same question answers on one run and declines on the next, measured repeatedly on
   2026-07-17. A cache freezes whichever reply came first, which is exactly what you want
   while iterating and exactly what you must not have while measuring rejection_rate.
2. *It would break erasure.* The key is derived from the question, and #28 exists to make a
   question unrecoverable — `redact_query` destroys the text and the salt. A cache file whose
   name encodes an unsalted hash of the question is a copy of that question that erasure
   cannot reach, sitting outside the database it was so carefully removed from.

So `Settings` refuses to boot with a cache configured outside `ENVIRONMENT=local`, in the same
way it refuses to boot without an issuer. The friction is the point, again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.services.answering import ExtractionResult, Extractor
from app.services.translation import MachineTranslation, Translator


def cache_key(*parts: str) -> str:
    """Content-addressed, like the PDF store: identical input, identical name.

    A NUL separator rather than a join on some character that could appear in a passage —
    ("ab", "c") and ("a", "bc") must not collide into the same key.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@dataclass(frozen=True)
class DiskCache:
    """A flat directory of JSON files. Not a database: it is a development convenience, and
    the moment it needs eviction, sharding or a schema it has outgrown its purpose."""

    root: Path

    def get(self, key: str) -> dict | None:
        path = self.root / f"{key}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt entry is a miss, never an error: the worst a broken cache may do is
            # cost one request.
            return None

    def put(self, key: str, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{key}.json"
        # Write-then-rename: a half-written file that a later run reads as a hit would be a
        # cache that invents an answer.
        temp = path.with_suffix(".json.part")
        temp.write_text(json.dumps(value), encoding="utf-8")
        temp.replace(path)


class CachingExtractor:
    """An Extractor that asks the model only for questions it has not seen."""

    def __init__(self, inner: Extractor, cache: DiskCache, *, model: str) -> None:
        self._inner = inner
        self._cache = cache
        self._model = model

    def extract(self, question: str, passages: list[str]) -> ExtractionResult | None:
        key = cache_key("extract", self._model, question, *passages)
        if (hit := self._cache.get(key)) is not None:
            # `None` is a real answer — the model declining is a result, not an absence — so
            # it is stored explicitly rather than inferred from a missing file.
            if hit.get("declined"):
                return None
            return ExtractionResult.model_validate(hit["result"])

        result = self._inner.extract(question, passages)
        self._cache.put(
            key,
            {"declined": True} if result is None else {"result": result.model_dump(mode="json")},
        )
        return result


class CachingTranslator:
    """A Translator that translates a given quote into a given language once."""

    def __init__(self, inner: Translator, cache: DiskCache, *, model: str) -> None:
        self._inner = inner
        self._cache = cache
        self._model = model

    def translate(self, quote: str, *, target_language: str) -> MachineTranslation:
        key = cache_key("translate", self._model, target_language, quote)
        if (hit := self._cache.get(key)) is not None:
            return MachineTranslation(**hit)

        # A failure is NOT cached: TranslationRateLimited means "try later", and remembering
        # it would turn a spent quota into a permanent one.
        result = self._inner.translate(quote, target_language=target_language)
        self._cache.put(
            key,
            {
                "source_quote": result.source_quote,
                "target_language": result.target_language,
                "text": result.text,
            },
        )
        return result
