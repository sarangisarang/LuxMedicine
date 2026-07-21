"""Memoise the model call, so asking the same thing twice costs one request.

**Why this is safe where caching an *answer* would not be.** The cache sits around the
extractor and the translator — the model call — and nothing else. The pipeline still runs in
full on every request: retrieval runs, #19 validates the quotes against the live chunks, the
hash-chained audit row is written, row-level security applies. Only the model's reply is
remembered. `app/api/queries.py` still has no unaudited path, because there is no path around
it — this replaces a network call, not a request.

**It self-invalidates, which is the part that makes it correct.** The key is
`sha256(model + prompt + question + passages)` — everything that determines the reply. If the
corpus changes, or a chunk is re-indexed, or retrieval ranks differently, the passages change
and so does the key. A stale answer for a changed corpus is not reachable; the miss is
structural, not a TTL someone has to tune.

**The prompt is in the key because it was not, and that was a bug waiting for its moment.**
The first version keyed on model + question + passages, which is every input except the one a
person is most likely to edit. Change a rule in SYSTEM_PROMPT to fix a behaviour, re-run the
eval, and every question would answer from disk: the old model's reply, reported as the new
prompt's result. A tool built to save requests will happily fake the experiment that needs
them — this cache had already done exactly that once, returning 0/21 flipped for a
determinism comparison it answered entirely from disk.

**Local development only, enforced at boot rather than documented — and for one reason, not
two.**

*It would break erasure.* The key is derived from the question, and #28 exists to make a
question unrecoverable: `redact_query` destroys the text and the salt. A cache file whose name
encodes an unsalted hash of the question is a copy of that question that erasure cannot reach,
sitting outside the database it was so carefully removed from. That is decisive on its own, so
`Settings` refuses to boot with a cache configured outside `ENVIRONMENT=local`, the same way it
refuses to boot without an issuer.

**This file used to give a second reason, and it was wrong.** It said the model is not
deterministic even at temperature 0, so a cache would freeze whichever reply came first and
falsify a rejection_rate run. Measured on 2026-07-17 with the cache off — six questions, three
identical runs, eighteen live calls: **0/6 flipped.** gemini-3.1-flash-lite at temperature 0
returned the same outcome and the same citation count every time. What looked like the model
wavering was the *input* moving underneath it: limit=8 vs limit=10, three different models in
one afternoon, a quota degrading, a corpus that grew by 1,436 chunks. Each of those changes the
passages, and the passages are half the question.

So this cache is more defensible than the note introducing it claimed: with the model in the
key, a hit returns what a live call would have returned. The residual caution worth keeping is
that determinism here is evidence at n=3 over six questions on one model, not a guarantee —
which is why the key carries the model, and why this still has no business outside a
developer's machine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.services.answering import ExtractionResult, Extractor
from app.core.vocabulary import Sector
from app.services.extractor_claude import system_prompt_for
from app.services.translation import SYSTEM_PROMPT as TRANSLATION_PROMPT
from app.services.translation import MachineTranslation, Translator


def prompt_fingerprint(prompt: str) -> str:
    """The prompt, condensed into the key. Editing a rule must be a cache miss — the reply is
    a function of the instructions as much as of the passages, and a run that answers the new
    prompt from the old prompt's replies is not a run."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


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

    def extract(
        self, question: str, passages: list[str], sector: Sector = Sector.MEDICAL
    ) -> ExtractionResult | None:
        # The sector reaches the key through the prompt fingerprint rather than as a part
        # of its own, and that is not a shortcut — it is the rule this cache already
        # states: the reply is a function of the instructions as much as of the passages,
        # so two different instruction sets must be two different keys. The clinical and
        # legal prompts are different text, so they already fingerprint apart.
        #
        # Adding the sector as a separate part as well would key the same call twice on
        # the same fact, and would go quietly wrong the day a sector is added that shares
        # another's prompt: two entries where the answer is identical, one of them always
        # cold. One source of truth for "what was the model told".
        key = cache_key(
            "extract",
            self._model,
            prompt_fingerprint(system_prompt_for(sector)),
            question,
            *passages,
        )
        if (hit := self._cache.get(key)) is not None:
            # `None` is a real answer — the model declining is a result, not an absence — so
            # it is stored explicitly rather than inferred from a missing file.
            if hit.get("declined"):
                return None
            return ExtractionResult.model_validate(hit["result"])

        result = self._inner.extract(question, passages, sector)
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
        key = cache_key(
            "translate", self._model, prompt_fingerprint(TRANSLATION_PROMPT),
            target_language, quote,
        )
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
