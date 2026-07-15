"""E5 prefix contract (#10).

These need no model. They pin the one thing about E5 that fails silently: passages must
be embedded with `passage: ` and queries with `query: `. Get it wrong and nothing raises
— the vectors are the right shape, the insert succeeds, the search returns results, just
worse ones. The bill arrives much later, as a corpus that needs re-embedding.
"""

import pytest

from app.services.embedding import PASSAGE_PREFIX, QUERY_PREFIX, E5Embedder, Embedder


class RecordingE5(E5Embedder):
    """The real prefix logic with the model cut out.

    Deliberately skips E5Embedder.__init__ — loading multilingual-e5-large to check
    string concatenation would put several gigabytes in CI's path for no added coverage.
    Everything below __init__ is the shipped code.
    """

    def __init__(self) -> None:  # noqa: D107 — intentionally does not call super()
        self.encoded: list[list[str]] = []
        self._dimension = 1024
        self._batch_size = 16
        self._model_name = "recording-stub"

    def _encode(self, prefixed: list[str]) -> list[list[float]]:
        self.encoded.append(list(prefixed))
        return [[0.0] * self._dimension for _ in prefixed]


def test_passages_are_prefixed_for_indexing():
    embedder = RecordingE5()
    embedder.embed_passages(["The target dose of enalapril is 20 mg."])

    assert embedder.encoded == [[f"{PASSAGE_PREFIX}The target dose of enalapril is 20 mg."]]


def test_queries_are_prefixed_for_searching():
    embedder = RecordingE5()
    embedder.embed_query("target dose of enalapril?")

    assert embedder.encoded == [[f"{QUERY_PREFIX}target dose of enalapril?"]]


def test_the_two_prefixes_are_not_the_same():
    """E5 is asymmetric on purpose. If these ever converge, the interface's whole reason
    for splitting passages from queries has gone."""
    assert PASSAGE_PREFIX != QUERY_PREFIX


def test_every_passage_in_a_batch_is_prefixed():
    """A batch is where a prefix goes missing quietly — one unprefixed chunk in six
    hundred degrades exactly one passage, and nothing reports it."""
    embedder = RecordingE5()
    embedder.embed_passages([f"chunk {i}" for i in range(50)])

    sent = embedder.encoded[0]
    assert len(sent) == 50
    assert all(text.startswith(PASSAGE_PREFIX) for text in sent)


def test_the_interface_offers_no_unprefixed_path():
    """The design claim, as a test: a caller cannot embed text without saying whether it
    is a passage or a query, so it cannot pick the wrong prefix — or forget one."""
    assert not hasattr(RecordingE5(), "embed")
    assert not hasattr(RecordingE5(), "encode")


def test_recording_stub_satisfies_the_protocol():
    assert isinstance(RecordingE5(), Embedder)


@pytest.mark.parametrize("text", ["", "  ", "ჰიპერტენზია", "Hypertonie", "hypertension"])
def test_prefixing_is_language_agnostic(text):
    """18 languages (#16) all go through the same prefix — it is E5's marker, not
    English grammar."""
    embedder = RecordingE5()
    embedder.embed_passages([text])
    assert embedder.encoded[0][0] == PASSAGE_PREFIX + text
