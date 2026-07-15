"""Grouping and escalation tests (#22, #23).

These are pure functions over SearchHits, so no database and no model. The escalation
rule is where the clinical judgement lives — every test here is about what must *not*
trigger a comparison.
"""

import uuid

from app.services.grouping import (
    comparable_groups,
    distinct_organisations,
    group_hits,
    should_compare,
)
from app.services.retrieval import SearchHit


def hit(
    *,
    org: str,
    version_label: str,
    version_id: uuid.UUID,
    distance: float,
    content: str = "guidance",
    is_superseded: bool = False,
    superseding_version_label: str | None = None,
    document_id: uuid.UUID | None = None,
) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(),
        document_version_id=version_id,
        document_id=document_id or uuid.uuid4(),
        document_title=f"{org} Guideline",
        issuing_org=org,
        version_label=version_label,
        section=None,
        page_start=1,
        page_end=1,
        content=content,
        distance=distance,
        is_superseded=is_superseded,
        superseding_version_label=superseding_version_label,
    )


ESC = uuid.uuid4()
AHA = uuid.uuid4()
ESC_OLD = uuid.uuid4()
ESC_HYPERTENSION = uuid.uuid4()


# --- #22: grouping -----------------------------------------------------------------


def test_hits_from_one_edition_form_one_group():
    groups = group_hits(
        [
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.1),
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.2),
        ]
    )

    assert len(groups) == 1
    assert len(groups[0].hits) == 2
    assert groups[0].issuing_org == "ESC"


def test_different_editions_form_different_groups():
    groups = group_hits(
        [
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.1),
            hit(org="AHA", version_label="2023", version_id=AHA, distance=0.2),
        ]
    )

    assert len(groups) == 2
    assert {g.issuing_org for g in groups} == {"ESC", "AHA"}


def test_one_organisation_can_hold_two_groups():
    """ESC publishes more than one guideline. A clinician needs to know which they are
    reading — escalation counts organisations, presentation shows editions."""
    groups = group_hits(
        [
            hit(org="ESC", version_label="HF 2021", version_id=ESC, distance=0.1),
            hit(org="ESC", version_label="HTN 2023", version_id=ESC_HYPERTENSION, distance=0.2),
        ]
    )

    assert len(groups) == 2
    assert {g.issuing_org for g in groups} == {"ESC"}


def test_groups_are_ordered_by_their_best_hit():
    """A clinician reads downward. The strongest match leads, regardless of who published
    it or how many hits each source contributed."""
    groups = group_hits(
        [
            hit(org="AHA", version_label="2023", version_id=AHA, distance=0.9),
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.1),
            hit(org="AHA", version_label="2023", version_id=AHA, distance=0.95),
        ]
    )

    assert [g.issuing_org for g in groups] == ["ESC", "AHA"]
    assert groups[0].best_rank == 0.1


def test_retrieval_order_survives_inside_a_group():
    groups = group_hits(
        [
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.1, content="first"),
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.3, content="second"),
        ]
    )

    assert [h.content for h in groups[0].hits] == ["first", "second"]


def test_staleness_travels_onto_the_group():
    groups = group_hits(
        [
            hit(
                org="ESC",
                version_label="2019",
                version_id=ESC_OLD,
                distance=0.1,
                is_superseded=True,
                superseding_version_label="2023",
            )
        ]
    )

    assert groups[0].is_superseded
    assert groups[0].superseding_version_label == "2023"


def test_no_hits_means_no_groups():
    assert group_hits([]) == []


# --- #23: escalation ---------------------------------------------------------------


def test_one_organisation_does_not_escalate():
    """Asking a model to find conflicts in a single body's guidance invents them, and
    costs a call for a case that cannot arise."""
    groups = group_hits(
        [
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.1),
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.2),
        ]
    )

    assert not should_compare(groups)


def test_two_organisations_escalate():
    groups = group_hits(
        [
            hit(org="ESC", version_label="2021", version_id=ESC, distance=0.1),
            hit(org="AHA", version_label="2023", version_id=AHA, distance=0.2),
        ]
    )

    assert should_compare(groups)


def test_two_guidelines_from_one_body_do_not_escalate():
    """The conservative presumption: an organisation harmonises its own documents. It can
    fail — see the known limit in grouping.py — but a false conflict teaches clinicians to
    close the banner, and then it is not there on the day it matters (#25)."""
    groups = group_hits(
        [
            hit(org="ESC", version_label="HF 2021", version_id=ESC, distance=0.1),
            hit(org="ESC", version_label="HTN 2023", version_id=ESC_HYPERTENSION, distance=0.2),
        ]
    )

    assert not should_compare(groups)


# --- the wrinkle the roadmap missed ------------------------------------------------


def test_an_edition_and_its_successor_do_not_escalate():
    """The motivating scenario: ESC 2021 saying 20 mg beside ESC 2023 saying 35 mg is an
    update, not a disagreement.

    Note what actually protects this, because it is not what the name suggests: both are
    ESC, so the same-organisation rule catches it and the supersession filter never gets
    a say. Removing that filter leaves this test green.

    The filter earns its place in the cross-body case below, where nothing else would
    stop history from arguing with current guidance.
    """
    groups = group_hits(
        [
            hit(
                org="ESC",
                version_label="2021",
                version_id=ESC_OLD,
                distance=0.1,
                is_superseded=True,
                superseding_version_label="2023",
            ),
            hit(org="ESC", version_label="2023", version_id=ESC, distance=0.2),
        ]
    )

    assert not should_compare(groups)
    assert distinct_organisations(groups) == {"ESC"}


def test_a_superseded_edition_of_another_body_does_not_escalate_either():
    """**This is the test the supersession filter exists for.**

    An archived AHA edition beside a current ESC one is history beside guidance, not two
    bodies disagreeing. Nothing else stops it: the organisations genuinely differ, so the
    same-organisation rule waves it through, and a comparison pass would put a retired
    AHA recommendation on equal footing with live ESC guidance — a conflict banner over
    a disagreement that no longer exists.

    Remove the filter and this fails. The scenario test above does not.
    """
    groups = group_hits(
        [
            hit(org="ESC", version_label="2023", version_id=ESC, distance=0.1),
            hit(
                org="AHA",
                version_label="2015",
                version_id=AHA,
                distance=0.2,
                is_superseded=True,
                superseding_version_label="2024",
            ),
        ]
    )

    assert not should_compare(groups)


def test_two_current_bodies_escalate_even_with_history_present():
    """History in scope must not suppress a real disagreement either."""
    groups = group_hits(
        [
            hit(org="ESC", version_label="2023", version_id=ESC, distance=0.1),
            hit(org="AHA", version_label="2024", version_id=AHA, distance=0.2),
            hit(
                org="ESC",
                version_label="2019",
                version_id=ESC_OLD,
                distance=0.3,
                is_superseded=True,
                superseding_version_label="2023",
            ),
        ]
    )

    assert should_compare(groups)
    assert distinct_organisations(groups) == {"ESC", "AHA"}


def test_comparable_groups_excludes_history():
    groups = group_hits(
        [
            hit(org="ESC", version_label="2023", version_id=ESC, distance=0.1),
            hit(
                org="ESC",
                version_label="2019",
                version_id=ESC_OLD,
                distance=0.2,
                is_superseded=True,
            ),
        ]
    )

    assert [g.version_label for g in comparable_groups(groups)] == ["2023"]
    assert len(groups) == 2, "both are still shown to the clinician"


def test_nothing_retrieved_does_not_escalate():
    assert not should_compare([])
