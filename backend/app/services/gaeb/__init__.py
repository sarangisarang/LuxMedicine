"""GAEB DA XML conversion — turn a bill of quantities from any source file into a valid `.x84`.

`.x84` is the GAEB DA XML *Angebotsabgabe* (offer submission): a Leistungsverzeichnis whose
positions carry the bidder's unit prices. The hard part is never the XML — that is a strict, tidy
schema — it is reading the positions faithfully out of whatever file they arrive in (Excel, an
existing GAEB file, a PDF). So the pipeline is deliberately split: readers produce the neutral
`BillOfQuantities` model, a human verifies/corrects it, and only then does `writer` emit the `.x84`.
Nothing is exported that a person has not confirmed — a wrong unit price in a construction bid is
real money, the same discipline as the clinical side.
"""

from app.services.gaeb.model import BillOfQuantities, Position

__all__ = ["BillOfQuantities", "Position"]
