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
    long_text: str | None = None
    # Optional one level of grouping (Titel/Los). Positions with the same section render under one
    # GAEB category; empty means a flat list. Kept a plain label for now — nested hierarchies later.
    section: str = ""

    @property
    def item_total(self) -> Decimal | None:
        """IT = Qty × UP, rounded to the cent. None when the position is unpriced."""
        if self.unit_price is None:
            return None
        return round_money(self.quantity * self.unit_price)


@dataclass(frozen=True)
class BillOfQuantities:
    """A whole LV: project label, currency, and the positions in order."""

    project_name: str
    positions: tuple[Position, ...] = field(default_factory=tuple)
    currency: str = "EUR"
    name: str = ""

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
