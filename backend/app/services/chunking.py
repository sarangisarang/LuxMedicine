"""Section-aware chunking (#9).

Two jobs, and they are not equally important.

**Keeping clinical context intact** is the safety-critical one. A chunk that ends at
"the target dose of enalapril is" and hands "20 mg twice daily, unless eGFR < 30" to the
next chunk has separated a dose from the condition that qualifies it. Retrieval can then
return either half on its own, and a clinician reads a dose without its contraindication.
This is solved by where we break and by overlap — not by section detection.

**Labelling the section** is the secondary one. It helps retrieval and makes a citation
legible. A missing label costs a little; a wrong label is worse than none, so detection
is conservative and returns nothing when unsure — the same reasoning as #25.

The heading rule is typographic first, because in a clinical corpus a regex cannot do
this job. These two lines are identical to `^\\d+(\\.\\d+)*\\s`:

    2.1 Pharmacological therapy          <- 13pt bold  : a heading
    2.5 mg may be used as a starting...  <- 10pt body   : a dose

Decimals at the start of a line are usually doses, not section numbers. Reading one as a
heading would break the text at exactly the wrong place — severing the dose from its
context, which is the hazard this module exists to prevent. Typography separates them;
nothing else in the text does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.extraction import ExtractedDocument, Line

# Roughly 300 tokens of English. Small enough to embed sharply, large enough to hold a
# recommendation and its qualifiers together.
TARGET_CHARS = 1200
MAX_CHARS = 1800

# Carried from the tail of the previous chunk. This is the belt to the braces: even when
# a break lands badly, the qualifying clause still appears in one chunk alongside its
# dose.
OVERLAP_CHARS = 200

# A heading is short. Prose that happens to be bold and numbered is not.
MAX_HEADING_CHARS = 90

_SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+){0,3})\.?\s+(\S.*)$")

# The dose guard. If what follows the number is a unit, the line is a measurement no
# matter how it is typeset.
_UNITS = re.compile(
    r"^(?:mg|g|kg|mcg|µg|ug|ng|mmol|mol|mEq|IU|U|mL|ml|L|l|%|mm|cm|m|"
    r"mmHg|mg/dL|mmol/L|mg/kg|min|h|hr|hrs|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)

# Any letter, not [A-Za-z]: the legal corpus is German and the clinical one is
# multilingual, so an ASCII-only test would reject "Änderung" and "Übersicht" as
# wordless and quietly resurrect the bug it exists to fix — on exactly the headings a
# German statute uses most.
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    char_start: int
    char_end: int
    page_start: int
    page_end: int
    section: str | None


def heading_of(line: Line, *, body_font_size: float) -> str | None:
    """Return the section label if this line is a heading, else None.

    Four conditions, all required — and the two that reject doses catch *different*
    doses, which was measured rather than assumed by disabling each in turn:

    - Without the unit guard, every emphasised measurement passes: "2.5 mg daily",
      "140 mmHg systolic", "12 weeks of therapy". Guidelines bold their doses precisely
      to make them stand out, so typography actively argues for the wrong answer here.
    - Without the typography check, body-set prose that opens with a section number
      passes — the case a regex-only approach ships with.

    Neither is redundant. Together they leave a heading needing to be numbered, short,
    typeset as a heading, and not a measurement.
    """
    text = line.text.strip()

    if not text or len(text) > MAX_HEADING_CHARS:
        return None

    match = _SECTION_NUMBER.match(text)
    if not match:
        return None

    number, remainder = match.groups()

    # The one that matters: "2.5 mg ..." dies here even when bold and short.
    if _UNITS.match(remainder):
        return None

    # A heading says something. This one is here because HOAI's Honorartafeln broke it:
    # a fee row reads "25 000 3 120 3 657 3 657 4 339 ...", which is short, starts with a
    # number, carries no unit token, and is typeset larger than the surrounding body — so
    # it satisfied every other condition and became a section label. 253 of 296 chunks
    # came back labelled with a row of digits, meaning every citation the legal corpus
    # produced would have named a nonsense section.
    #
    # Requiring a letter is narrow enough to be safe in the clinical corpus too: a real
    # heading has words in it, in every document either corpus holds. `table_extraction`
    # already applies exactly this test to a candidate column label for the same reason.
    #
    # This does not make the fee tables *quotable* — that is #48's problem and the table
    # guard's job. It only stops them being mistaken for structure.
    if not _HAS_LETTER.search(remainder):
        return None

    # Typography. Strictly larger than body, or bold at body size — some publishers set
    # sub-headings bold without enlarging them.
    larger = line.font_size > body_font_size
    emphasised = line.is_bold and line.font_size >= body_font_size
    if not (larger or emphasised):
        return None

    return f"{number} {remainder}"


def _sentence_ends(text: str) -> bool:
    return text.rstrip().endswith((".", "?", "!", ":", ";"))


def _overlap_lines(buffer: list[Line]) -> list[Line]:
    """The trailing lines to carry into the next chunk, up to OVERLAP_CHARS.

    Whole lines, not a character slice: a chunk starting mid-word embeds badly and
    quotes worse. Never the entire buffer — carrying everything would mean the next
    chunk starts where this one did and the packer never advances.
    """
    carried: list[Line] = []
    total = 0
    for line in reversed(buffer[1:]):
        if carried and total + len(line.text) > OVERLAP_CHARS:
            break
        carried.insert(0, line)
        total += len(line.text)
    return carried


def chunk_document(doc: ExtractedDocument) -> list[TextChunk]:
    """Pack lines into chunks, breaking at sections and preferring sentence ends.

    Chunk spans are resolved through the extraction ledger, so a chunk that crosses a
    page break reports both pages rather than guessing one.
    """
    chunks: list[TextChunk] = []
    buffer: list[Line] = []
    section: str | None = None

    def emit() -> None:
        nonlocal buffer
        if not buffer:
            return

        start, end = buffer[0].char_start, buffer[-1].char_end
        page_start, page_end = doc.pages_for_span(start, end)

        chunks.append(
            TextChunk(
                ordinal=len(chunks),
                text=doc.text[start:end],
                char_start=start,
                char_end=end,
                page_start=page_start,
                page_end=page_end,
                section=section,
            )
        )
        buffer = []

    for line in doc.lines:
        if not line.text.strip():
            continue

        found = heading_of(line, body_font_size=doc.body_font_size)
        if found is not None:
            emit()  # close out the previous section before the label changes
            section = found
            buffer = [line]  # the heading leads its own chunk, so it is retrievable
            continue

        if buffer:
            projected = line.char_end - buffer[0].char_start
            # Prefer to break where a sentence already ended. Past MAX_CHARS take what
            # we can get, rather than let a chunk grow without bound.
            if projected > TARGET_CHARS and (
                _sentence_ends(buffer[-1].text) or projected > MAX_CHARS
            ):
                carried = _overlap_lines(buffer)
                emit()
                # Overlapping spans are fine: char_start still points at real text, so
                # pages_for_span stays exact for both chunks.
                buffer = carried

        buffer.append(line)

    emit()
    return chunks
