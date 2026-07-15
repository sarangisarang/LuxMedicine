"""Query expansion tests (#16)."""

import uuid

import pytest
from sqlalchemy import insert

from app.models.alias import DrugAlias
from app.services.synonyms import expand_query, load_aliases

ALIASES = {"renitec": "enalapril", "vasotec": "enalapril", "glucophage": "metformin"}


# --- the expansion itself ----------------------------------------------------------


def test_a_brand_name_brings_its_generic():
    result = expand_query("Renitec dose", ALIASES)

    assert result.applied == {"renitec": "enalapril"}
    assert "enalapril" in result.expanded
    assert result.was_expanded


def test_the_brand_is_kept_not_replaced():
    """A guideline may name the brand too. Replacing would trade one blind spot for
    another."""
    result = expand_query("Renitec dose", ALIASES)
    assert "Renitec" in result.expanded


def test_an_unrecognised_query_is_untouched():
    result = expand_query("target dose of enalapril", ALIASES)

    assert result.expanded == result.original
    assert not result.was_expanded


def test_the_generic_is_not_repeated_when_already_present():
    """Writing "Renitec (enalapril)" is normal. Appending enalapril again would tilt the
    embedding toward that one word for no reason."""
    result = expand_query("Renitec enalapril dose", ALIASES)
    assert not result.was_expanded


def test_two_brands_of_the_same_generic_add_it_once():
    result = expand_query("Renitec or Vasotec", ALIASES)

    assert result.applied == {"renitec": "enalapril", "vasotec": "enalapril"}
    assert result.expanded.count("enalapril") == 1


def test_brands_of_different_generics_both_expand():
    result = expand_query("Renitec and Glucophage", ALIASES)
    assert set(result.applied.values()) == {"enalapril", "metformin"}


@pytest.mark.parametrize("query", ["RENITEC", "renitec", "Renitec", "ReNiTeC"])
def test_matching_ignores_case(query):
    assert expand_query(f"{query} dose", ALIASES).applied == {"renitec": "enalapril"}


@pytest.mark.parametrize("query", ["Reniteco dose", "xrenitec", "prerenitecs"])
def test_matching_respects_word_boundaries(query):
    """A substring match would drag a wrong drug into the query silently — the exact
    failure this feature exists to prevent, arriving from the other side."""
    assert not expand_query(query, ALIASES).was_expanded


def test_expansion_is_reported_not_hidden():
    """Without `applied`, "why did asking about Renitec return enalapril?" has no answer,
    and an expansion nobody can inspect is one nobody can correct."""
    result = expand_query("Renitec dose", ALIASES)
    assert result.applied["renitec"] == "enalapril"


def test_no_aliases_means_no_work():
    result = expand_query("Renitec dose", {})
    assert result.expanded == "Renitec dose"


# --- the table ---------------------------------------------------------------------


async def test_aliases_load_from_the_database(session):
    await session.execute(
        insert(DrugAlias),
        [
            {
                "id": uuid.uuid4(),
                "alias": f"probe-{uuid.uuid4().hex[:8]}",
                "generic_name": "enalapril",
                "source": "test fixture",
            }
        ],
    )
    await session.commit()

    loaded = await load_aliases(session)
    assert any(generic == "enalapril" for generic in loaded.values())


async def test_one_brand_cannot_mean_two_generics(session):
    """The unique constraint is the guarantee. A duplicate alias would make the wrong
    drug reachable by name — permanently, and without a symptom."""
    from sqlalchemy.exc import IntegrityError

    alias = f"dup-{uuid.uuid4().hex[:8]}"
    await session.execute(
        insert(DrugAlias),
        [{"id": uuid.uuid4(), "alias": alias, "generic_name": "enalapril", "source": "first"}],
    )
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(
            insert(DrugAlias),
            [{"id": uuid.uuid4(), "alias": alias, "generic_name": "metformin", "source": "second"}],
        )
    await session.rollback()


async def test_an_uppercase_alias_is_rejected_by_the_database(session):
    """Matching lowercases the query, so an uppercase row would simply never fire —
    silently. The CHECK makes that unrepresentable rather than unlikely."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await session.execute(
            insert(DrugAlias),
            [
                {
                    "id": uuid.uuid4(),
                    "alias": f"UPPER-{uuid.uuid4().hex[:6]}",
                    "generic_name": "enalapril",
                    "source": "test",
                }
            ],
        )
    await session.rollback()
