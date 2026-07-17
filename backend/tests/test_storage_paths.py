"""Where a version's bytes are, for whoever is asking (0014).

`storage_uri` used to hold an absolute path — the path on the laptop that ingested the PDF.
That read as correct for months and was not: it is a location on one machine, not a location.
The API in a container mounts the same bytes at `/app/storage/...`, `C:\\Users\\...` does not
exist on Linux, and every source PDF returned 500. The row never changed; only the reader did.

So these tests are about one property: **the same row must find its bytes from anywhere.**
"""

from pathlib import Path

from app.services import storage


def test_a_relative_uri_resolves_under_whatever_root_is_configured(tmp_path):
    """The property the container needed. The same stored value, two roots, two machines."""
    on_host = storage.resolve("60/6067abc.pdf", root=Path("/host/storage"))
    in_container = storage.resolve("60/6067abc.pdf", root=Path("/app/storage"))

    assert on_host == Path("/host/storage/60/6067abc.pdf")
    assert in_container == Path("/app/storage/60/6067abc.pdf")


def test_relative_uri_is_what_ingestion_stores(tmp_path):
    root = tmp_path / "storage"
    path = storage.path_for("60abcdef" + "0" * 56, root=root)

    uri = storage.relative_uri(path, root=root)

    assert not Path(uri).is_absolute()
    # Forward slashes even when written on Windows, or the URI does not resolve on Linux.
    assert "\\" not in uri
    assert storage.resolve(uri, root=root) == path


def test_a_legacy_absolute_uri_from_another_machine_still_resolves(tmp_path):
    """Tolerance for rows written before 0014 — and for any that escape it.

    A database that has not been migrated must degrade, not break: the layout below the root
    is still the layout, whoever wrote the prefix.
    """
    root = tmp_path / "storage"
    (root / "60").mkdir(parents=True)
    real = root / "60" / "6067abc.pdf"
    real.write_bytes(b"%PDF-1.4\n")

    foreign = r"C:\Users\someone\Desktop\LuxMedicine\backend\storage\60\6067abc.pdf"

    assert storage.resolve(foreign, root=root) == real


def test_an_absolute_uri_that_exists_is_left_alone(tmp_path):
    """On the machine that wrote it, the old value still names the right file — resolving it
    to somewhere else would break the very case that used to work."""
    root = tmp_path / "storage"
    root.mkdir()
    here = tmp_path / "elsewhere.pdf"
    here.write_bytes(b"%PDF-1.4\n")

    assert storage.resolve(str(here), root=root) == here
