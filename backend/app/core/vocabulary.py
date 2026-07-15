"""Controlled vocabulary for issuing organisations.

`issuing_org` is the field conflict detection rests on entirely. If ingestion writes
"ESC" one day and "European Society of Cardiology" the next, the escalation rule in
#23 never fires: retrieval sees two organisations as one, finds nothing to compare,
and the feature does nothing while appearing to work. That is the worst failure mode
available — a silent one, in the feature meant to catch contradictions.

Deliberately a Python enum rather than a Postgres enum. A database enum would enforce
the vocabulary at the strongest possible layer, but every new guideline body — and
there are hundreds — would need a migration to onboard. Since the API is the only
writer, Pydantic validation gives the same guarantee at a cost that does not grow.

**Known limit:** this is closed, so a clinic's own internal protocol has nowhere to go.
That is fine while the corpus is international guidelines, and it breaks the moment
B2B (#31) brings local protocols with it — at which point this becomes an
`organisations` table, not a longer enum.
"""

from enum import StrEnum


class IssuingOrg(StrEnum):
    # Cardiology
    ESC = "ESC"  # European Society of Cardiology
    AHA = "AHA"  # American Heart Association
    ACC = "ACC"  # American College of Cardiology

    # Broad / national bodies
    WHO = "WHO"  # World Health Organization
    NICE = "NICE"  # UK National Institute for Health and Care Excellence

    # Specialty
    ADA = "ADA"  # American Diabetes Association
    EASD = "EASD"  # European Association for the Study of Diabetes
    ESMO = "ESMO"  # European Society for Medical Oncology
    ASCO = "ASCO"  # American Society of Clinical Oncology
    IDSA = "IDSA"  # Infectious Diseases Society of America
    KDIGO = "KDIGO"  # Kidney Disease: Improving Global Outcomes
    GINA = "GINA"  # Global Initiative for Asthma

    @property
    def region(self) -> str | None:
        """Best-effort default, overridable per document.

        Conflict detection groups by organisation, not region — but region is what
        makes a conflict legible to a clinician ("Europe says X, the US says Y"),
        so it is worth defaulting rather than leaving blank.
        """
        return _REGIONS.get(self)


_REGIONS: dict[IssuingOrg, str] = {
    IssuingOrg.ESC: "EU",
    IssuingOrg.EASD: "EU",
    IssuingOrg.ESMO: "EU",
    IssuingOrg.AHA: "US",
    IssuingOrg.ACC: "US",
    IssuingOrg.ADA: "US",
    IssuingOrg.ASCO: "US",
    IssuingOrg.IDSA: "US",
    IssuingOrg.NICE: "UK",
    IssuingOrg.WHO: "Global",
    IssuingOrg.KDIGO: "Global",
    IssuingOrg.GINA: "Global",
}
