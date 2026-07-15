"""Where the clinical text actually goes (#6's premise, applied to #18).

**Asserted from the resolved URL, never from the config.** That distinction is the whole
file. google-gemini/gemini-cli#27984 — open, filed June 2026 — is a client that is handed
`europe-west3`, silently routes to the global endpoint because an API key is present, and
*keeps displaying the region in its config*. Google's own documentation on the global
endpoint: you "can't control or know which region your ML processing requests are sent
to". So a system that reports its configured region reports a wish.

Same shape as everything else this project has caught: HTTP 200 on a broken chain, a
mutation harness reporting SURVIVED for a mutant that would not compile, a verifier
reading its own cache, a superuser skipping every RLS policy in silence. A mechanism that
looks like success when it is absent.

**No credentials needed.** Constructing a Vertex client resolves the endpoint without
touching the network — measured — so these run anywhere, including CI. What needs
credentials is *calling* it, and that is not what is under test here.
"""

import pytest

pytest.importorskip("google.genai", reason="needs the 'gemini' extra")

from app.services.extractor_gemini import (  # noqa: E402
    EU_PROCESSING_HOSTS,
    GeminiExtractor,
    ProcessingLeavesTheEU,
)


@pytest.fixture(autouse=True)
def a_key_that_is_never_used(monkeypatch):
    """The Developer-API client will not construct without *a* key. It does not need a
    valid one: nothing here calls the API, and that is why these tests run in CI, where
    there is no key and never should be one."""
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key-nothing-is-called")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)


def vertex(location: str) -> GeminiExtractor:
    """A Vertex client pinned to a location. The project is fictional and never called."""
    return GeminiExtractor(vertex_project="not-a-real-project", vertex_location=location)


# --- the promise #6 made ------------------------------------------------------------


def test_the_developer_api_does_not_keep_processing_in_the_eu():
    """The path we can currently reach, and it is not compliant with our own premise.

    Recorded as a test rather than a comment because it is the state of the system: an
    API key resolves to generativelanguage.googleapis.com and there is no regional
    pinning on that path at all. #6 self-hosts the embedding model so clinical text stays
    in the EU. Sending the question *and* the retrieved guideline passages here is a
    bigger transfer than the clinician identity #30 refused Auth0 over.
    """
    extractor = GeminiExtractor()

    assert extractor.endpoint == "https://generativelanguage.googleapis.com/"
    assert not extractor.processes_in_eu

    with pytest.raises(ProcessingLeavesTheEU, match="not a Vertex regional endpoint"):
        extractor.refuse_unless_eu_processing()


def test_a_european_vertex_region_does():
    extractor = vertex("europe-west3")

    assert extractor.endpoint == "https://europe-west3-aiplatform.googleapis.com/"
    assert extractor.processes_in_eu
    extractor.refuse_unless_eu_processing()  # does not raise


@pytest.mark.parametrize("location", ["us-central1", "asia-northeast1", "us-east4"])
def test_a_non_european_region_is_refused(location):
    extractor = vertex(location)

    assert not extractor.processes_in_eu
    with pytest.raises(ProcessingLeavesTheEU):
        extractor.refuse_unless_eu_processing()


def test_the_vertex_global_endpoint_is_refused():
    """The one Google warns about in its own docs, and the one that reads as configured.

    `location="global"` is a location. It looks set. It resolves to
    aiplatform.googleapis.com, where processing happens somewhere nobody can name.
    """
    extractor = vertex("global")

    assert extractor.endpoint == "https://aiplatform.googleapis.com/"
    assert not extractor.processes_in_eu
    with pytest.raises(ProcessingLeavesTheEU):
        extractor.refuse_unless_eu_processing()


# --- the trap itself ----------------------------------------------------------------


def test_an_api_key_cannot_be_combined_with_a_region():
    """#27984, checked against the Python SDK rather than assumed from the JS one.

    In `@google/genai` this combination silently wins for the API key: the location is
    dropped, the request goes global, and the config still says europe-west3. Python
    refuses it outright. That is the correct behaviour — and it is not a reason to trust
    the config, which is why `endpoint` exists.
    """
    from google import genai

    with pytest.raises(ValueError, match="mutually exclusive"):
        genai.Client(vertexai=True, api_key="not-a-real-key", location="europe-west3")


def test_residency_is_read_from_the_url_not_the_argument():
    """The property under test is the resolved URL. If someone later reimplements
    `processes_in_eu` by comparing `vertex_location` to a list, this still passes — and
    that is the bug #27984 is. So: the argument and the URL must be checked to agree,
    which only the URL can do."""
    extractor = vertex("europe-west3")

    assert "europe-west3" in extractor.endpoint
    assert extractor.endpoint.startswith("https://europe-west3-aiplatform")


# --- the allow-list ------------------------------------------------------------------


def test_the_eu_list_is_hosts_not_a_substring_test():
    """A `"europe" in url` check passes for anything containing the word — including a
    host nobody vetted and a typo'd region. Every entry here is a full host."""
    for host in EU_PROCESSING_HOSTS:
        assert host.endswith("-aiplatform.googleapis.com")
        assert host.startswith("europe-")

    assert "aiplatform.googleapis.com" not in EU_PROCESSING_HOSTS, (
        "the bare global host must never be on the EU list"
    )


def test_an_unlisted_european_region_is_refused_not_guessed():
    """`europe-west12` may well be European. Nobody checked, and for this purpose
    "unverified" and "not EU" are the same answer — the alternative is a system that
    infers a legal property from a string prefix."""
    extractor = vertex("europe-west12")

    assert not extractor.processes_in_eu


# --- what this does not claim --------------------------------------------------------


def test_the_refusal_is_not_wired_into_the_constructor():
    """Deliberate. Measuring an extractor against invented fixtures is legitimate, and
    refusing at construction would mean the only way to test the thing is to already be
    compliant. The refusal belongs where real clinical text enters — a deployment's
    decision. This makes the fact checkable; it does not make it true."""
    extractor = GeminiExtractor()  # the non-compliant path

    assert not extractor.processes_in_eu, "constructing it is allowed"
    assert extractor.endpoint, "and it will happily tell you where it points"
