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

from pathlib import Path

import pytest

from app.cli.evaluate import Outcome, Question, load_questions, report


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

    def test_withdrawn_questions_name_their_lost_source(self, questions):
        """#49: the KDIGO/NICE questions moved to withdrawn_source. The source string must say
        so, or a later reader restores them to answerable and the firewall test evaporates."""
        withdrawn = [q for q in questions if q.expect == "withdrawn_source"]
        assert len(withdrawn) >= 8, "expected the 8 KDIGO/NICE questions"
        for q in withdrawn:
            assert "withdrawn" in (q.source or "").lower(), q.id
            assert any(k in (q.source or "") for k in ("KDIGO", "NICE")), q.id

    def test_the_48_questions_expect_the_pages_that_answer(self, questions):
        """p102 is the barrier table. If someone ever "fixes" #48 by widening this to include
        102, the check dies quietly and the dangerous answer scores green again — so pin it."""
        for qid in ("contraception-migraine-aura", "contraception-migraine-aura-de"):
            q = next(x for x in questions if x.id == qid)
            assert q.expect_pages == [69, 123], qid
            assert 102 not in q.expect_pages, f"{qid}: p102 is the BARRIER table (#48)"

    def test_both_buckets_are_populated(self, questions):
        """The whole point of the design: without not_covered there is no way to tell a
        correct refusal from a wrong one. The answerable floor dropped from 15 to 10 when #49
        withdrew KDIGO/NICE — the shrink is real and recorded, not a regression to fix by
        re-labelling questions back."""
        assert sum(q.expect == "answerable" for q in questions) >= 9
        assert sum(q.expect == "not_covered" for q in questions) >= 5
        assert sum(q.expect == "withdrawn_source" for q in questions) >= 8
