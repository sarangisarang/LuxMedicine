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


class Sector(StrEnum):
    """Which body of knowledge a document belongs to.

    This is a retrieval boundary, not a label. A question about heart failure must never
    retrieve §35 HOAI, and a question about Honorarzonen must never retrieve a cardiology
    guideline — not because the prompt discourages it, but because the SQL never returns
    the other sector's chunks. Mixing them is not merely irrelevant: an extractive system
    quotes whatever it is handed, so a stray statute in a clinical result set is a
    verbatim, correctly-cited, and completely wrong answer.

    A document's sector is *derived* from its issuing organisation (see
    `IssuingOrg.sector`) rather than passed in beside it. Two independent fields that must
    agree are two fields that will eventually disagree, and the disagreement would be
    invisible — the document would simply stop being findable, or start answering the
    wrong questions.
    """

    MEDICAL = "medical"
    LEGAL = "legal"


class IssuingOrg(StrEnum):
    # Cardiology
    ESC = "ESC"  # European Society of Cardiology
    AHA = "AHA"  # American Heart Association
    ACC = "ACC"  # American College of Cardiology

    # Broad / national bodies
    WHO = "WHO"  # World Health Organization
    NICE = "NICE"  # UK National Institute for Health and Care Excellence
    NHLBI = "NHLBI"  # US National Heart, Lung, and Blood Institute (NIH)
    CDC = "CDC"  # US Centers for Disease Control and Prevention

    # Specialty
    ADA = "ADA"  # American Diabetes Association
    EASD = "EASD"  # European Association for the Study of Diabetes
    ESMO = "ESMO"  # European Society for Medical Oncology
    ASCO = "ASCO"  # American Society of Clinical Oncology
    IDSA = "IDSA"  # Infectious Diseases Society of America
    KDIGO = "KDIGO"  # Kidney Disease: Improving Global Outcomes
    GINA = "GINA"  # Global Initiative for Asthma

    # --- Legal ---------------------------------------------------------------
    # German federal law as published by the Bundesamt für Justiz on
    # gesetze-im-internet.de. One organisation for all of it, not one per statute, and
    # that is deliberate: conflict detection (#23) escalates when a result set spans two
    # organisations, and HOAI, VgV and GWB do not contradict each other — they compose.
    # Splitting them would fire a comparison pass on every question that touches both a
    # fee schedule and the procurement law it sits under, and present a hierarchy as a
    # disagreement.
    #
    # Only sources free of copyright by §5 UrhG (amtliche Werke) are filed here. A
    # publisher's Textausgabe of the same statute is a copyrighted compilation and does
    # not become an amtliches Werk by containing one.
    BUNDESRECHT = "Bundesrecht"

    @property
    def sector(self) -> Sector:
        """Which corpus this organisation's documents belong to.

        Defaults to MEDICAL: the enum was medical-only for its whole life, and a new
        entry that forgets to declare itself should land in the sector the system was
        built for rather than silently leak into the other one.
        """
        return _SECTORS.get(self, Sector.MEDICAL)

    @property
    def region(self) -> str | None:
        """Best-effort default, overridable per document.

        Conflict detection groups by organisation, not region — but region is what
        makes a conflict legible to a clinician ("Europe says X, the US says Y"),
        so it is worth defaulting rather than leaving blank.
        """
        return _REGIONS.get(self)


_SECTORS: dict[IssuingOrg, Sector] = {
    IssuingOrg.BUNDESRECHT: Sector.LEGAL,
}


_REGIONS: dict[IssuingOrg, str] = {
    IssuingOrg.ESC: "EU",
    IssuingOrg.EASD: "EU",
    IssuingOrg.ESMO: "EU",
    IssuingOrg.AHA: "US",
    IssuingOrg.ACC: "US",
    IssuingOrg.ADA: "US",
    IssuingOrg.ASCO: "US",
    IssuingOrg.IDSA: "US",
    IssuingOrg.NHLBI: "US",
    IssuingOrg.CDC: "US",
    IssuingOrg.NICE: "UK",
    IssuingOrg.WHO: "Global",
    IssuingOrg.KDIGO: "Global",
    IssuingOrg.GINA: "Global",
    IssuingOrg.BUNDESRECHT: "DE",
}
