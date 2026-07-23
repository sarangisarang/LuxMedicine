"""When one self-describing row replaces another — decided by a rule, not by eye.

`augment_tables` is insert-only, and correctly so: a cited chunk must survive for the audit
trail to resolve (migration 0008 enforces it with a trigger). But that means every fix to the
extractor lays down a corrected row *beside* the flawed one, and both stay retrievable. After
the label-completion fix, 88 truncated rows sat next to their completed twins — so
"ii. Systolic ≥160 mm Hg or" could still out-rank "Systolic ≥160 mm Hg or diastolic ≥100 mm Hg"
and hand a clinician half a threshold.

Marking the old row superseded solves both halves at once: it leaves the chunk in place for
the audit trail while taking it out of retrieval, needs no exception for cited rows, and is
reversible.

**The pairing is the dangerous part, so it is a rule with a test rather than a judgement.**
The measurement that found these rows had a bug on exactly this path — it compared raw labels
and missed that a completed row also loses its enumerator, under-counting 60 against 88. A bug
of the same shape could as easily over-count, and over-counting here silently retires a row
that was never replaced. So the rule is deliberately narrow, and every clause is load-bearing:

  same page          a row is replaced by its own re-reading, never by a similar row elsewhere
  identical cells    the full category vector must match character for character
  old is truncated   a complete row is not a draft of anything and can never be superseded
  new extends old    the new label must literally begin with the old one and be longer

Anything else — a shorter label, an equal one, a different category vector — is not a
replacement, and this returns False rather than guessing.
"""

from __future__ import annotations

import re

# "<label> — <cells>" for the two self-describing families. The cells half is compared whole,
# so a difference of a single category is a different row.
_ROW = re.compile(
    r"^(?P<label>.+?) — (?P<cells>(?:Cu-IUD|LNG-IUD|Implant|DMPA|POP|CHC|COC|CIC|P): .*|Honorarzone .*)$"
)

_ENUMERATOR = re.compile(r"^(?:[ivxlcdm]+|[a-z]|\d{1,2})[.)]\s+", re.IGNORECASE)

# Both languages, because `_ROW` matches both families. The fee rows do not truncate today
# (measured: 337 rows, 0 unbalanced, 0 dangling — extract_tables joins a wrapped cell's lines
# instead of dropping them), but a rule that claims a family and then cannot read its language
# is a trap for whoever changes the fee extractor next.
_DANGLING_TAIL = re.compile(
    r"(?:\b(?:e\.g\.|i\.e\.|and|or|the|of|with|for|in|to"
    r"|und|oder|der|die|das|des|bei|mit|von|für|zur|zum)|,)$",
    re.IGNORECASE,
)


def _core(label: str) -> str:
    """A label reduced to the printed row's own words.

    Both normalisations are required and both were learned from getting it wrong: a completed
    row often gains a parent segment ("Elevated blood pressure levels — Systolic …") *and*
    loses its bullet, because the enumerator is stripped when a parent is carried. Comparing
    raw labels therefore misses the rows the fix improved most.
    """
    return _ENUMERATOR.sub("", label.split(" — ")[-1].strip()).strip()


def looks_truncated(label: str) -> bool:
    """Whether a label stops mid-phrase — an unclosed bracket or a word no phrase ends on."""
    stripped = _core(label)
    if not stripped:
        return False
    return stripped.count("(") != stripped.count(")") or bool(_DANGLING_TAIL.search(stripped))


def replaces(old_content: str, new_content: str) -> bool:
    """Whether `new_content` is the same printed row as `old_content`, read further.

    Callers must additionally require the two chunks to be on the same page; that is not
    checked here because content alone cannot establish it.
    """
    old, new = _ROW.match(old_content or ""), _ROW.match(new_content or "")
    if not old or not new:
        return False
    if old.group("cells") != new.group("cells"):
        return False

    old_core, new_core = _core(old.group("label")), _core(new.group("label"))
    if not old_core or old_core == new_core:
        return False
    if not looks_truncated(old.group("label")):
        return False
    return new_core.startswith(old_core) and len(new_core) > len(old_core)
