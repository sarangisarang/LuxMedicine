"""The neutral bill-of-quantities model that every reader targets and the writer consumes.

One model, so a new input format is a new reader and nothing else — the `.x84` writer never learns
where a position came from. Money is `Decimal`, never `float`: a bid total is summed from many unit
prices and a float would drift the last cent, which in a tender is a real discrepancy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

# GAEB carries quantities to three decimals and money to two — the DA XML convention. Rounding is
# fixed here, not at each call site, so every position and the grand total round the same way.
_QTY_EXP = Decimal("0.001")
_MONEY_EXP = Decimal("0.01")


def round_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_EXP, rounding=ROUND_HALF_UP)


def round_qty(value: Decimal) -> Decimal:
    return value.quantize(_QTY_EXP, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Position:
    """One LV line. `oz` is the Ordnungszahl (position number, e.g. "01.02.0030"); `unit` is the
    GAEB quantity unit (QU, e.g. "m2", "St", "psch"). `unit_price` is None for a priced-blank request
    (.x83) and set for an offer (.x84) — the writer refuses to emit a .x84 with a missing price."""

    oz: str
    short_text: str
    quantity: Decimal
    unit: str
    unit_price: Decimal | None = None
    # What the SOURCE file printed as this line's total, when it carried such a column. Never
    # exported — a .x84 derives the total from Qty × UP — but kept as evidence: comparing it with
    # `item_total` is how a mis-read column is caught instead of quietly becoming a wrong bid.
    source_total: Decimal | None = None
    long_text: str | None = None
    # Optional one level of grouping (Titel/Los). Positions with the same section render under one
    # GAEB category; empty means a flat list. Kept a plain label for now — nested hierarchies later.
    section: str = ""

    # --- DIN 276 cost groups, and the source text -------------------------------------------------
    # The Kostengruppe this position sits under, outermost first: ("500 Außenanlagen und
    # Freiflächen", "520 Gründung, Unterbau", "522 Gründungen und Bodenplatten", …) up to the fourth
    # level. Read from the document's own headings — a position belongs to whichever KG it was
    # printed beneath.
    kg: tuple[str, ...] = ()
    # A partial amount the source prints on the line, when it carries one.
    teilbetrag: Decimal | None = None
    # Every value EXACTLY as the source file wrote it. The requirement is that quantities, units,
    # partial amounts, unit prices and totals are taken over unchanged and displayed — no
    # calculation, no rounding, no re-formatting. The parsed Decimals above exist only so the .x84
    # can carry typed values and so a mis-read column can be detected; what a person sees is this.
    quantity_text: str = ""
    unit_price_text: str = ""
    total_text: str = ""
    teilbetrag_text: str = ""

    @property
    def item_total(self) -> Decimal | None:
        """The line total. The source file's own figure when it printed one — taken over, never
        recomputed — and Qty × UP only as a fallback for a file that carries no total column."""
        if self.source_total is not None:
            return self.source_total
        if self.unit_price is None:
            return None
        return round_money(self.quantity * self.unit_price)

    @property
    def computed_total(self) -> Decimal | None:
        """Qty × UP. Not shown and not exported — used only to test the source's figure against the
        columns we read, which is how a mis-assigned price column is caught."""
        if self.unit_price is None:
            return None
        return round_money(self.quantity * self.unit_price)

    @property
    def disagrees_with_source(self) -> bool:
        """True when our Qty × UP does not match the total the source file printed for this line.

        The file checks our arithmetic: if the two differ, a column was read wrongly — most often a
        line total taken for a unit price, which inflates the bid by the quantity. Surfaced rather
        than silently accepted."""
        if self.source_total is None or self.computed_total is None:
            return False
        return abs(self.computed_total - self.source_total) > Decimal("0.02")


@dataclass(frozen=True)
class Entry:
    """One ROW of the source document, exactly as it stood there.

    The converted view has to be the document, not a filtered version of it: a Kostenberechnung is
    cost-group headings, source entries ("1  LV Freiflächen") and positions interleaved, and each
    carries figures of its own. Keeping only the positions threw away the group totals and the
    structure they belong to. So every row is carried across verbatim, with `kind` recording what the
    row is, and nothing is summed — the amounts here are the ones the file printed.

    The fields mirror the source's own columns: KG/OZ, DIN 276 / Quelleinträge, Menge/Einheit,
    Teilbetrag / EP, Gesamt EUR.
    """

    kind: str  # "kg" (a DIN 276 heading), "entry" (a Quelleintrag), or "position"
    number: str  # the KG number, the source entry's number, or the Ordnungszahl
    text: str
    menge_einheit: str = ""
    teilbetrag_ep: str = ""
    gesamt: str = ""
    level: int = 0  # the DIN 276 level for a "kg" row; 0 otherwise
    kg: tuple[str, ...] = ()  # the cost-group path this row sits under
    long_text: str | None = None


@dataclass(frozen=True)
class BillOfQuantities:
    """A whole LV: project label, currency, the positions, and — for display — every row of the
    source document in the order it was printed."""

    project_name: str
    positions: tuple[Position, ...] = field(default_factory=tuple)
    currency: str = "EUR"
    name: str = ""
    # The document as it stands: headings, source entries and positions interleaved. `positions` is
    # what the .x84 exports; `entries` is what a person is shown, and it is the whole file.
    entries: tuple[Entry, ...] = field(default_factory=tuple)

    @property
    def is_priced(self) -> bool:
        """True when every position carries a unit price — the precondition for a valid .x84 offer."""
        return bool(self.positions) and all(p.unit_price is not None for p in self.positions)

    @property
    def total(self) -> Decimal:
        """Grand total, summed from the per-item totals (unpriced positions count as zero)."""
        return round_money(
            sum((p.item_total or Decimal(0) for p in self.positions), Decimal(0))
        )

    @property
    def sections(self) -> tuple[str, ...]:
        """The distinct section labels in first-seen order (empty string included if any position
        is ungrouped) — the writer walks these to build the category tree."""
        seen: list[str] = []
        for p in self.positions:
            if p.section not in seen:
                seen.append(p.section)
        return tuple(seen)
