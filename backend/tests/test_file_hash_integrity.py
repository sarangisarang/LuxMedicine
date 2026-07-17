"""`file_hash` is what a version is recorded as containing (#46).

`storage.py` opens with the claim: "the store cannot disagree with
`document_versions.file_hash` about what a version contains". For two of the three documents
in the real corpus that was never true — the value was `uuid4().hex` zero-padded to sha256's
width, written by whatever seeded the corpus before the ingest CLI existed. It survived
because nothing ever asserted the property. This file asserts it.

The distinction the repair tool turns on, and therefore the thing worth testing hardest: a
placeholder was never a claim about the bytes and can be corrected, while a real hash that
stopped matching is a file that changed after registration — an incident, and correcting it
would erase the only evidence of it.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.cli.repair_hashes import Finding, inspect, is_placeholder
from app.core.vocabulary import IssuingOrg
from app.models.document import DocumentVersion
from app.services import storage
from app.services.ingestion import RegistrationRequest, register_version

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# --- the shape that is provably not a hash --------------------------------------------


def test_a_uuid_padded_to_64_is_recognised_as_a_placeholder():
    """Exactly what was found in the corpus: uuid4().hex + 32 zeros."""
    assert is_placeholder(uuid.uuid4().hex + "0" * 32)


def test_the_two_real_corpus_values_are_recognised():
    """The literal values from KDIGO 2012 and NICE 2018, kept so a future rewrite of
    is_placeholder cannot stop recognising the case it was written for."""
    assert is_placeholder("60672814ddda43748752654fcd7b116300000000000000000000000000000000")
    assert is_placeholder("d32d36f0d02b44778c398cebb0be41cf00000000000000000000000000000000")


def test_a_real_sha256_is_never_a_placeholder():
    """The guard that stops the repair from eating an incident. If this ever returns True,
    the tool would silently overwrite the evidence that a file changed."""
    assert not is_placeholder(hashlib.sha256(PDF).hexdigest())
    assert not is_placeholder(hashlib.sha256(b"anything").hexdigest())


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0" * 64,                                   # zeros, but no uuid in front
        "z" * 32 + "0" * 32,                        # not hex
        uuid.uuid4().hex,                           # a uuid, but not padded — wrong length
        uuid.uuid4().hex + "1" * 32,                # padded with something else
        uuid.uuid1().hex + "0" * 32,                # a uuid, but not version 4
        hashlib.sha256(PDF).hexdigest()[:32] + "0" * 32,  # half a real hash + zeros
    ],
)
def test_anything_else_is_left_alone(value):
    """When in doubt the tool must do nothing: a column whose job is to be trusted has no
    safe guess."""
    assert not is_placeholder(value)


# --- the property itself, against a real registration ---------------------------------


async def test_a_version_registered_through_the_cli_path_hashes_its_bytes(session, tmp_path):
    """The invariant, asserted directly. Cheap, and its absence is why #46 survived.

    Not a test of the hashing function — a test that what lands in the column is the hash of
    what lands in the store.
    """
    root = tmp_path / "storage"
    file_hash = hashlib.sha256(PDF).hexdigest()
    final = storage.path_for(file_hash, root=root)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(PDF)

    version = await register_version(
        session,
        RegistrationRequest(
            title=f"Integrity {uuid.uuid4().hex[:6]}",
            issuing_org=IssuingOrg.ESC,
            version_label="2026",
            file_hash=file_hash,
            storage_uri=storage.relative_uri(final, root=root),
        ),
    )
    await session.commit()

    stored = (
        await session.execute(select(DocumentVersion).where(DocumentVersion.id == version.id))
    ).scalar_one()

    resolved = storage.resolve(stored.storage_uri, root=root)
    assert resolved.is_file()
    assert hashlib.sha256(resolved.read_bytes()).hexdigest() == stored.file_hash, (
        "the store disagrees with file_hash about what this version contains — the one "
        "thing storage.py promises it cannot do"
    )
    assert not is_placeholder(stored.file_hash)


# --- what the tool reports -------------------------------------------------------------


def _finding(stored: str, actual: str | None) -> Finding:
    return Finding(version_id=uuid.uuid4(), label="2012", stored=stored, actual=actual, uri="x.pdf")


def test_a_placeholder_is_repairable_and_a_changed_file_is_not():
    """The whole point of the tool, as one assertion.

    A placeholder was never a claim about the bytes; a real hash that no longer matches is a
    claim that has been broken, and those need opposite responses.
    """
    real = hashlib.sha256(PDF).hexdigest()

    assert _finding(uuid.uuid4().hex + "0" * 32, real).state == "placeholder"
    assert _finding(hashlib.sha256(b"other").hexdigest(), real).state == "CONTENT_CHANGED"
    assert _finding(real, real).state == "ok"
    assert _finding(real, None).state == "file_missing"


async def test_inspect_reports_every_version(session, tmp_path):
    root = tmp_path / "storage"
    file_hash = hashlib.sha256(PDF).hexdigest()
    final = storage.path_for(file_hash, root=root)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(PDF)

    await register_version(
        session,
        RegistrationRequest(
            title=f"Inspect {uuid.uuid4().hex[:6]}",
            issuing_org=IssuingOrg.ESC,
            version_label="2027",
            # A placeholder, exactly as the seed script left them.
            file_hash=uuid.uuid4().hex + "0" * 32,
            storage_uri=storage.relative_uri(final, root=root),
        ),
    )
    await session.commit()

    findings = await inspect(session, root)
    ours = [f for f in findings if f.label == "2027"]

    assert ours and ours[0].state == "placeholder"
    assert ours[0].actual == file_hash
