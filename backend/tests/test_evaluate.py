"""The measuring instrument, measured.

`app/cli/evaluate.py` decides what counts as the system working. It had no tests, which is
how #48 stayed inside `answered 15/18` for four consecutive runs: the system quoted
`ii. With aura 1` from CDC p102 — the BARRIER-method table — in answer to a question about
migraine with aura, where the answer that matters is the combined-hormonal category 4 on
p69/p123. Verbatim, correctly cited, zero rejections, scored a success.

A wrong number in a report is worse than no report, because a report is what you stop
checking.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import insert

from app.cli.evaluate import Outcome, Question, load_questions, report
from app.core.config import get_settings

DIM = get_settings().embedding_dim


def make(
    *,
    expect: str = "answerable",
    groups: int = 1,
    pages: list[str] | None = None,
    expect_pages: list[int] | None = None,
    error: str | None = None,
    tags: list[str] | None = None,
) -> Outcome:
    return Outcome(
        id="q",
        expect=expect,
        tags=tags or [],
        no_answer_reason=None,
        groups=groups,
        citations=len(pages or []),
        orgs=["CDC"],
        pages=pages if pages is not None else ["1"],
        rejected=0,
        diagnoses=[],
        elapsed_s=1.0,
        expect_pages=expect_pages or [],
        error=error,
    )


class TestAnsweredOffSource:
    """#48. The check that separates the answer from the answer-shaped thing."""

    def test_the_real_48_case(self):
        """The exact shape that scored `answered` four times: p102, expecting p69/p123."""
        o = make(pages=["102"], expect_pages=[69, 123])
        assert o.answered is True  # it DID answer — that was never in doubt
        assert o.answered_off_source is True
        assert o.verdict == "answered_off_source"

    def test_answering_from_an_expected_page_is_a_success(self):
        o = make(pages=["69"], expect_pages=[69, 123])
        assert o.answered_off_source is False
        assert o.verdict == "answered"

    def test_any_expected_page_clears_it_even_beside_a_wrong_one(self):
        """Documented as `any`, so pin `any` — a docstring is not a test.

        p69 answers and p102 misleads; this metric reports the answer arrived. It does NOT
        claim the p102 quote is harmless — see the docstring on `answered_off_source`.
        """
        o = make(pages=["102", "69"], expect_pages=[69, 123])
        assert o.answered_off_source is False
        assert o.verdict == "answered"

    def test_a_question_without_expect_pages_is_never_off_source(self):
        """Most questions have prose answers in several places. Silence must not mean guilt —
        otherwise adding the field would retroactively fail every existing question."""
        o = make(pages=["999"], expect_pages=[])
        assert o.answered_off_source is False
        assert o.verdict == "answered"

    def test_declining_is_not_off_source(self):
        """Nothing was shown, so nothing was shown from the wrong place. It is
        wrongly_declined — a different failure with a different fix."""
        o = make(groups=0, pages=[], expect_pages=[69])
        assert o.answered_off_source is False
        assert o.verdict == "wrongly_declined"

    def test_answered_with_no_page_numbers_is_not_off_source(self):
        """`bool(cited)` guards this: no evidence is not evidence of wrongness."""
        o = make(pages=[], expect_pages=[69])
        assert o.answered_off_source is False

    def test_non_numeric_pages_do_not_crash_the_run(self):
        """A run must survive one odd row — see run_one's except."""
        o = make(pages=["ix", "102"], expect_pages=[69])
        assert o.answered_off_source is True

    def test_not_covered_ignores_expect_pages(self):
        """`answered_uncovered` outranks it: answering at all is the failure there, and where
        from does not soften it."""
        o = make(expect="not_covered", pages=["102"], expect_pages=[69])
        assert o.verdict == "answered_uncovered"

    def test_an_error_outranks_everything(self):
        o = make(pages=["102"], expect_pages=[69], error="boom")
        assert o.verdict == "error"


class TestWithdrawnSource:
    """#49. Answerable only from a withdrawn document; a decline is the firewall holding."""

    def test_declining_a_withdrawn_source_question_is_the_firewall_working(self):
        o = make(expect="withdrawn_source", groups=0, pages=[])
        assert o.verdict == "withdrawn_declined"

    def test_answering_it_is_the_dangerous_leak(self):
        """The corpus no longer contains a CKD answer, so an answer is conjured from
        elsewhere — the answered_uncovered danger under a name that flags the cause."""
        o = make(expect="withdrawn_source", groups=1, pages=["21"])
        assert o.verdict == "withdrawn_leaked"

    def test_it_never_pads_correctly_declined(self):
        """The whole reason for a separate bucket: a withdrawn decline must not inflate the
        genuinely-never-covered number, or 6/8 stops being comparable to its own history."""
        r = report(
            [
                make(expect="not_covered", groups=0, pages=[]),  # correctly_declined 1/1
                make(expect="withdrawn_source", groups=0, pages=[]),  # must NOT touch it
            ]
        )
        assert r["not_covered"]["correctly_declined"] == "1/1"
        assert r["withdrawn_source"]["correctly_declined"] == "1/1"
        assert r["withdrawn_source"]["count"] == 1
        assert r["withdrawn_source"]["leaked"] == 0

    def test_a_leak_is_counted(self):
        r = report([make(expect="withdrawn_source", groups=1, pages=["21"])])
        assert r["withdrawn_source"]["leaked"] == 1
        assert r["withdrawn_source"]["correctly_declined"] == "0/1"


class TestReport:
    """The four numbers must stay four. A metric that folds is a metric that hides."""

    def test_off_source_is_reported_and_not_subtracted_from_answered(self):
        """`answered` keeps counting it, because that IS what happened — the clinician got an
        answer. The two lines are read together; folding one into the other is how #48 hid."""
        r = report([make(pages=["102"], expect_pages=[69, 123])])
        assert r["answerable"]["answered"] == "1/1"
        assert r["answerable"]["answered_off_source"] == "1/1"

    def test_off_source_rate_counts_only_questions_that_declared_pages(self):
        """Denominator = questions where the check applies. Including the rest would dilute
        the number toward zero and make it unreadable at corpus scale."""
        r = report(
            [
                make(pages=["102"], expect_pages=[69]),  # off source
                make(pages=["5"]),  # no expect_pages — not in the denominator
                make(pages=["7"]),
            ]
        )
        assert r["answerable"]["answered_off_source"] == "1/1"
        assert r["answerable"]["answered"] == "3/3"

    def test_glyph_45_is_still_kept_apart(self):
        """Regression guard: #45 must not fold into the model's score just because #48 added
        a line next to it."""
        r = report([make(tags=["glyph-45"], groups=0), make(groups=1)])
        assert r["answerable"]["glyph_45_answered"] == "0/1"
        assert r["answerable"]["clean_prose_answered"] == "1/1"


class TestQuestionsYaml:
    """The real file. It is data, and data this load-bearing is code."""

    @staticmethod
    @pytest.fixture(scope="class")
    def questions() -> list[Question]:
        path = Path(__file__).resolve().parents[1] / "eval" / "questions.yaml"
        return load_questions(path)

    def test_it_parses(self, questions):
        assert len(questions) >= 26

    def test_every_expect_is_a_known_label(self, questions):
        """A typo here would silently drop a question out of every bucket."""
        assert {q.expect for q in questions} <= {
            "answerable",
            "not_covered",
            "withdrawn_source",
        }

    # Documents withdrawn from the index, and why. Each addition here must be paired with the
    # questions that depended on it, in the same change — a corpus and its ground truth updated
    # separately means the next run reports the difference as the system changing.
    WITHDRAWN_SOURCES = ("KDIGO", "NICE", "NHLBI")

    def test_withdrawn_questions_name_their_lost_source(self, questions):
        """A withdrawn question's `source` must say which document went and that it went, or a
        later reader relabels it `answerable` and the firewall test quietly evaporates.

        Asserts the property, not the roster: the list above is data, so withdrawing a fourth
        document is one line here rather than a test that fails for being out of date. It also
        keeps the reasons distinguishable — KDIGO and NICE went for licence (#49), NHLBI for
        corpus focus (2026-07-23) — because `withdrawn_declined` means different things and a
        reader of the number is entitled to know which."""
        withdrawn = [q for q in questions if q.expect == "withdrawn_source"]
        assert len(withdrawn) >= 8
        for q in withdrawn:
            assert "withdrawn" in (q.source or "").lower(), q.id
            assert any(k in (q.source or "") for k in self.WITHDRAWN_SOURCES), q.id

    def test_the_48_questions_expect_the_pages_that_answer(self, questions):
        """p102 is the barrier table. If someone ever "fixes" #48 by widening this to include
        102, the check dies quietly and the dangerous answer scores green again — so pin it."""
        for qid in ("contraception-migraine-aura", "contraception-migraine-aura-de"):
            q = next(x for x in questions if x.id == qid)
            assert q.expect_pages == [69, 123], qid
            assert 102 not in q.expect_pages, f"{qid}: p102 is the BARRIER table (#48)"

    def test_both_buckets_are_populated(self, questions):
        """The whole point of the design: without `not_covered` there is no way to tell a
        correct refusal from a wrong one, and without `answerable` there is nothing to catch a
        system that has learned to refuse everything.

        The floors are deliberately low and are NOT a target. `answerable` has fallen 15 -> 10
        -> 5 as documents were withdrawn (KDIGO/NICE for licence, NHLBI for focus); each drop
        is a real corpus shrinking, recorded, and never to be "fixed" by relabelling a question
        back. What must never happen is a bucket emptying, because then the run stops being
        interpretable — which is what these assert."""
        answerable = sum(q.expect == "answerable" for q in questions)
        assert answerable >= 5, (
            f"only {answerable} answerable questions — below this the run cannot show that the "
            "system still answers at all, and a refuse-everything regression would score clean"
        )
        assert sum(q.expect == "not_covered" for q in questions) >= 5
        assert sum(q.expect == "withdrawn_source" for q in questions) >= 8


class TestGroundTruthCheck:
    """`expect_pages` is coupled to ingestion and only this warns about it.

    Supersession retires a row from retrieval while leaving it in the table, and a re-read row
    can land on a different page than the one it replaced — so a page written against an older
    corpus goes stale in two opposite-looking ways, and both surface as a verdict about the
    SYSTEM.

    **The first version of this check could not fire.** It counted chunks on a page across the
    whole corpus, where page 69 holds 110 active chunks from 20 unrelated documents; only 42 of
    the first 500 page numbers had no active chunk anywhere. It reported "0 stale" and that
    meant "cannot discriminate", not "fresh". So the test that matters is not "does it stay
    quiet on a good corpus" — it is "does it speak when the page really is empty for THAT
    source", which is the case the unscoped version was blind to.
    """

    @staticmethod
    async def _seed(session, *, title: str, org: str, page: int, superseded: bool):
        from app.models.chunk import Chunk
        from app.models.document import Document, DocumentVersion, VersionStatus

        marker = uuid.uuid4().hex[:8]
        document = Document(title=f"{title} {marker}", issuing_org=org, region="EU")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_label="2026",
            file_hash=hashlib.sha256(marker.encode()).hexdigest(),
            storage_uri=f"/store/{marker}.pdf",
            status=VersionStatus.ACTIVE,
        )
        session.add(version)
        await session.flush()

        first, second = uuid.uuid4(), uuid.uuid4()
        await session.execute(
            insert(Chunk),
            [
                {
                    "id": first,
                    "document_version_id": version.id,
                    "ordinal": 0,
                    "page_start": page,
                    "page_end": page,
                    "section": None,
                    "content": f"seeded row on page {page}",
                    "embedding": [0.0] * DIM,
                },
                {
                    "id": second,
                    "document_version_id": version.id,
                    "ordinal": 1,
                    # The replacement lands on a DIFFERENT page — the drift being guarded.
                    "page_start": page + 1,
                    "page_end": page + 1,
                    "section": None,
                    "content": f"re-read row, now on page {page + 1}",
                    "embedding": [0.0] * DIM,
                },
            ],
        )
        # Pointed after both exist, exactly as augment_tables does it — the FK is not
        # deferrable, so a row cannot reference a replacement that is not inserted yet.
        if superseded:
            from sqlalchemy import update

            await session.execute(
                update(Chunk).where(Chunk.id == first).values(superseded_by=second)
            )
        await session.commit()
        return document.title

    async def test_it_warns_when_the_expected_page_holds_only_retired_rows(self, session):
        """The case the unscoped version could never reach."""
        from app.cli.evaluate import Question, check_ground_truth

        page = 4242  # far from any real fixture's page numbers
        title = await self._seed(
            session, title="Ground Truth Probe", org="ESC", page=page, superseded=True
        )
        question = Question(
            id="probe", text="?", expect="answerable", source=title, expect_pages=[page]
        )

        warnings = await check_ground_truth(session, [question])

        assert warnings, "a page whose only rows are retired must be reported"
        assert "probe" in warnings[0] and str(page) in warnings[0]
        assert "retired" in warnings[0]

    async def test_it_stays_quiet_when_the_page_still_has_active_rows(self, session):
        from app.cli.evaluate import Question, check_ground_truth

        page = 4343
        title = await self._seed(
            session, title="Ground Truth Fresh", org="ESC", page=page, superseded=False
        )
        question = Question(
            id="fresh", text="?", expect="answerable", source=title, expect_pages=[page]
        )

        assert await check_ground_truth(session, [question]) == []

    async def test_a_question_without_a_source_is_reported_not_skipped(self, session):
        """An expectation that cannot be scoped is unverifiable, and silence would read as
        verified — which is the failure mode this whole check exists for."""
        from app.cli.evaluate import Question, check_ground_truth

        question = Question(id="nosource", text="?", expect="answerable", expect_pages=[7])
        warnings = await check_ground_truth(session, [question])

        assert warnings and "nosource" in warnings[0] and "source" in warnings[0]

    async def test_the_scope_is_the_document_not_the_page_number(self, session):
        """The bug itself: another document having content on the same page must not clear the
        warning. Page numbers repeat across a corpus — 20 documents share page 69."""
        from app.cli.evaluate import Question, check_ground_truth

        page = 4444
        title = await self._seed(
            session, title="Scoped Probe", org="ESC", page=page, superseded=True
        )
        # A DIFFERENT document with a live chunk on the very same page.
        await self._seed(session, title="Unrelated Doc", org="AHA", page=page, superseded=False)

        question = Question(
            id="scoped", text="?", expect="answerable", source=title, expect_pages=[page]
        )
        warnings = await check_ground_truth(session, [question])

        assert warnings, (
            "an unrelated document's chunk on the same page silenced the warning — the check "
            "is counting page numbers instead of the question's own source"
        )


class TestCorpusContainsScope:
    """`_corpus_contains` decides `synthesis` vs `paraphrase`, and `synthesis` sets
    `is_invention` — the most serious number this report produces.

    It asked the whole chunks table, including withdrawn documents and superseded rows, neither
    of which can reach a model. Two baselines around the NHLBI withdrawal produced a
    byte-identical rejected quote from the same page with the same counts, diagnosed
    `paraphrase, invention=False` in one run and `synthesis, invention=True` in the other: the
    model had not changed, the table had. A fabrication verdict moved because of text nobody
    could have read.
    """

    async def _seed(self, session, *, text: str, status, superseded: bool = False, reference: bool = False):
        from sqlalchemy import update

        from app.models.chunk import Chunk
        from app.models.document import Document, DocumentVersion

        marker = uuid.uuid4().hex[:8]
        document = Document(title=f"Scope {marker}", issuing_org="ESC", region="EU")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_label="2026",
            file_hash=hashlib.sha256(marker.encode()).hexdigest(),
            storage_uri=f"/store/{marker}.pdf",
            status=status,
        )
        session.add(version)
        await session.flush()

        first, second = uuid.uuid4(), uuid.uuid4()
        await session.execute(
            insert(Chunk),
            [
                {
                    "id": first, "document_version_id": version.id, "ordinal": 0,
                    "page_start": 1, "page_end": 1, "section": None,
                    "content": text, "embedding": [0.0] * DIM, "is_reference": reference,
                },
                {
                    "id": second, "document_version_id": version.id, "ordinal": 1,
                    "page_start": 2, "page_end": 2, "section": None,
                    "content": "replacement row", "embedding": [0.0] * DIM,
                },
            ],
        )
        if superseded:
            await session.execute(update(Chunk).where(Chunk.id == first).values(superseded_by=second))
        await session.commit()

    async def test_an_active_chunk_counts(self, session):
        from app.cli.evaluate import _corpus_contains
        from app.models.document import VersionStatus

        phrase = f"visible marker {uuid.uuid4().hex[:10]}"
        await self._seed(session, text=f"prose containing {phrase} here", status=VersionStatus.ACTIVE)
        assert await _corpus_contains(session, phrase) is True

    async def test_a_withdrawn_document_does_not_count(self, session):
        """It cannot be retrieved, so the model cannot have assembled a quote from it."""
        from app.cli.evaluate import _corpus_contains
        from app.models.document import VersionStatus

        phrase = f"withdrawn marker {uuid.uuid4().hex[:10]}"
        await self._seed(session, text=f"prose containing {phrase} here", status=VersionStatus.WITHDRAWN)
        assert await _corpus_contains(session, phrase) is False

    async def test_a_superseded_chunk_does_not_count(self, session):
        """Retired from retrieval and kept only for the audit trail — the exact class of row
        that flipped a fabrication verdict between two runs."""
        from app.cli.evaluate import _corpus_contains
        from app.models.document import VersionStatus

        phrase = f"superseded marker {uuid.uuid4().hex[:10]}"
        await self._seed(
            session, text=f"prose containing {phrase} here", status=VersionStatus.ACTIVE, superseded=True
        )
        assert await _corpus_contains(session, phrase) is False

    async def test_a_reference_chunk_does_not_count(self, session):
        """#50 keeps bibliography out of retrieval, so it is not available to assemble from."""
        from app.cli.evaluate import _corpus_contains
        from app.models.document import VersionStatus

        phrase = f"reference marker {uuid.uuid4().hex[:10]}"
        await self._seed(
            session, text=f"prose containing {phrase} here", status=VersionStatus.ACTIVE, reference=True
        )
        assert await _corpus_contains(session, phrase) is False
