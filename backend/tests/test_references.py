"""The bibliography detector (app/services/references.py, #50).

Precision is the bar: flagging a guidance chunk would remove an answer, the invisible failure
this project fears most. The GOOD cases are real guidance from the corpus (some carrying inline
citations, which must not trip it); the BAD cases are real reference lines pulled from the
chunks that buried the contraception-postpartum answer.
"""

from __future__ import annotations

import pytest

from app.services.references import looks_like_reference

# Real reference-list content — MUST be flagged.
REFERENCES = [
    "91. Tepper NK, Phillips SJ, Kapp N, Gaffield ME, Curtis KM. Combined hormonal "
    "contraceptive use among women with... Obstet Gynecol 2017;129:e102.",
    "100. Gaffield ME, Kapp N, Ravi A. Use of combined oral contraceptives post abortion. "
    "Contraception 2009;80:355-62.",
    "58. Kim C, Nguyen AT, Berry-Bibee E, Ermias Y, Gaffield ME, Kapp N. Systemic hormonal "
    "contraception. Contraception 2016.",
    "Agertoft L, Pedersen S. Effects of long-term treatment with an inhaled corticosteroid on "
    "growth. N Engl J Med 2000;343:1064-9.",
    "1998;4(6):841–8.\nLeach CL, Davidson PJ, Boudreau RJ. Improved airway targeting with a "
    "hydrofluoroalkane.",
]

# Real guidance from the corpus — MUST NOT be flagged, including ones with inline citations.
GUIDANCE = [
    "A person aged ≥35 years who smokes ≥15 cigarettes per day should not use COCs because of "
    "unacceptable health risk.",
    "Patients with obesity (BMI ≥30 kg/m2) can use implants (U.S. MEC 1).",
    "Comment: Theoretical concern exists that LNG-IUDs might enhance progression of neoplasia.",
    "Otherwise, a patient who is ≥21 days postpartum and whose menstrual cycle has not returned "
    "needs to abstain from sexual intercourse or use barrier methods (44).",
    "The Expert Panel recommends that multifaceted allergen education and control interventions "
    "be considered for patients with asthma.",
    "consideration should be given to transitioning from CHCs to a progestin-only or "
    "nonhormonal method",
]


@pytest.mark.parametrize("content", REFERENCES)
def test_reference_entries_are_flagged(content):
    assert looks_like_reference(content) is True, content


@pytest.mark.parametrize("content", GUIDANCE)
def test_guidance_is_never_flagged(content):
    # Precision is the safety-critical direction: a false positive here hides an answer.
    assert looks_like_reference(content) is False, content


def test_empty_is_not_a_reference():
    assert looks_like_reference("") is False
    assert looks_like_reference("   \n  ") is False


def test_a_lone_inline_citation_does_not_flag_guidance():
    # A guidance sentence ending in "(44)" or one journal mention must stay searchable — the
    # 0.55 line-fraction is what separates a citation-bearing sentence from a citation list.
    assert looks_like_reference(
        "Patients also should be counseled about back-up contraception (31)."
    ) is False
