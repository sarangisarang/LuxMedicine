"""Alias loader tests.

The loader's job is mostly refusal. A brand that means two different drugs is the failure
this exists to prevent — and the tempting fix (take the newer row) is a wrong-drug bug
with no symptom, because the wrong answer looks exactly like the right one.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.alias import DrugAlias
from app.services.alias_loader import AliasRow, load_aliases_from_csv, load_aliases_from_rows, read_csv

SOURCE = "Test Registry, 2026-06"


def row(alias: str, generic: str) -> AliasRow:
    return AliasRow(alias=alias, generic_name=generic)


def unique(stem: str) -> str:
    return f"{stem}{uuid.uuid4().hex[:6]}"


# --- the refusal this exists for ---------------------------------------------------


async def test_a_brand_that_means_two_drugs_is_reported_not_resolved(session):
    """The load-bearing behaviour.

    One of the two claims is wrong and nothing here can tell which. Taking the newer row
    would silently change what a brand means — after answers have already been given
    using the old meaning.
    """
    alias = unique("renitec-")

    first = await load_aliases_from_rows(session, [row(alias, "enalapril")], source=SOURCE)
    await session.commit()
    assert first.inserted == 1

    second = await load_aliases_from_rows(
        session, [row(alias, "metformin")], source="A Different Registry"
    )

    assert second.inserted == 0
    assert second.needs_a_human
    [conflict] = second.conflicts
    assert conflict.existing_generic == "enalapril"
    assert conflict.incoming_generic == "metformin"
    assert conflict.existing_source == SOURCE, "a reader needs to know who claimed what"

    await session.commit()
    stored = (
        await session.execute(select(DrugAlias).where(DrugAlias.alias == alias))
    ).scalar_one()
    assert stored.generic_name == "enalapril", "the existing claim stands until a human rules"


async def test_two_rows_in_one_file_disagreeing_are_both_refused(session):
    """Neither row is trustworthy, so neither is applied. Taking the first would be as
    arbitrary as taking the last."""
    alias = unique("dilacor-")

    report = await load_aliases_from_rows(
        session, [row(alias, "diltiazem"), row(alias, "digoxin")], source=SOURCE
    )
    await session.commit()

    assert report.needs_a_human
    assert len(report.conflicts) == 1
    assert (
        await session.execute(select(DrugAlias).where(DrugAlias.alias == alias))
    ).scalar_one_or_none() is None


async def test_re_loading_the_same_claim_is_not_a_conflict(session):
    """A registry re-published unchanged must be idempotent, or every refresh looks like
    a crisis."""
    alias = unique("vasotec-")

    await load_aliases_from_rows(session, [row(alias, "enalapril")], source=SOURCE)
    await session.commit()

    again = await load_aliases_from_rows(session, [row(alias, "enalapril")], source=SOURCE)
    await session.commit()

    assert again.inserted == 0
    assert again.unchanged == 1
    assert not again.needs_a_human


# --- provenance --------------------------------------------------------------------


async def test_a_source_is_required(session):
    with pytest.raises(ValueError, match="source is required"):
        await load_aliases_from_rows(session, [row(unique("x-"), "enalapril")], source="   ")


async def test_the_source_lands_on_every_row(session):
    """A wrong alias answers confidently about the wrong drug. Someone has to be
    answerable for each one."""
    alias = unique("renitec-")
    await load_aliases_from_rows(session, [row(alias, "enalapril")], source=SOURCE)
    await session.commit()

    stored = (await session.execute(select(DrugAlias).where(DrugAlias.alias == alias))).scalar_one()
    assert stored.source == SOURCE


# --- validation, before anything is written ----------------------------------------


@pytest.mark.parametrize(
    "alias,generic,reason",
    [
        ("RENITEC", "enalapril", "lowercase"),
        ("", "enalapril", "empty alias"),
        ("renitec", "", "empty generic_name"),
        ("enalapril", "Enalapril", "same as the generic"),
        ("ab", "enalapril", "shorter than 3"),
    ],
)
async def test_bad_rows_are_named_before_anything_is_written(session, alias, generic, reason):
    """The CHECK constraints would catch most of these — as an IntegrityError halfway
    through a 3,000-row file, naming no row. This names every one first."""
    report = await load_aliases_from_rows(session, [row(alias, generic)], source=SOURCE)
    await session.rollback()

    assert report.inserted == 0
    [rejected] = report.rejected
    assert reason in rejected.reason


async def test_an_uppercase_alias_would_have_been_a_silent_no_op(session):
    """Matching lowercases the query, so an uppercase row never fires — it would sit in
    the table looking loaded and do nothing."""
    report = await load_aliases_from_rows(session, [row("RENITEC", "enalapril")], source=SOURCE)
    await session.rollback()

    assert report.rejected and "lowercase" in report.rejected[0].reason


async def test_a_short_alias_is_refused_rather_than_defaulted_through(session):
    """Word boundaries are not enough protection for a two-letter brand. If a registry has
    one, it needs deciding on."""
    report = await load_aliases_from_rows(session, [row("ac", "acetylcysteine")], source=SOURCE)
    await session.rollback()

    assert report.rejected


async def test_good_rows_load_alongside_bad_ones_being_reported(session):
    good = unique("renitec-")
    report = await load_aliases_from_rows(
        session, [row(good, "enalapril"), row("BAD", "metformin")], source=SOURCE
    )
    await session.commit()

    assert report.inserted == 1
    assert len(report.rejected) == 1


# --- CSV ---------------------------------------------------------------------------


async def test_a_csv_loads(session, tmp_path):
    alias = unique("renitec-")
    path = tmp_path / "aliases.csv"
    path.write_text(f"alias,generic_name\n{alias},enalapril\n", encoding="utf-8")

    report = await load_aliases_from_csv(session, path, source=SOURCE)
    await session.commit()

    assert report.inserted == 1


def test_a_csv_missing_a_column_is_named(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("alias\nrenitec\n", encoding="utf-8")

    with pytest.raises(ValueError, match="generic_name"):
        read_csv(path)


def test_blank_lines_are_skipped_not_rejected(tmp_path):
    path = tmp_path / "gappy.csv"
    path.write_text("alias,generic_name\nrenitec,enalapril\n,\n", encoding="utf-8")

    rows, _ = read_csv(path)
    assert len(rows) == 1


def test_a_bom_does_not_break_the_header(tmp_path):
    """Registries are exported from Excel. Excel writes a BOM, and utf-8 (not
    utf-8-sig) turns the first column name into something that never matches."""
    path = tmp_path / "excel.csv"
    path.write_bytes(b"\xef\xbb\xbfalias,generic_name\nrenitec,enalapril\n")

    rows, _ = read_csv(path)
    assert len(rows) == 1
    assert rows[0].alias == "renitec"


# --- what an empty table means -----------------------------------------------------


async def test_an_empty_table_means_brand_names_do_not_work(session, embedder=None):
    """Not a test of the loader — a test of the claim that the loader matters.

    With no aliases, expansion is a no-op and "Renitec" reaches whatever the embedding
    thinks it is nearest, which measured *below chance* against the real model (#16).
    The mechanism being built and tested does not make the system work; the data does.
    """
    from app.services.synonyms import expand_query, load_aliases

    aliases = await load_aliases(session)
    result = expand_query("Renitec dose", {})

    assert not result.was_expanded
    assert result.expanded == "Renitec dose"
    assert isinstance(aliases, dict)
