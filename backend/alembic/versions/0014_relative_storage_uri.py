"""storage_uri is a location under the root, not a location on one machine.

**What was wrong.** `document_versions.storage_uri` held an absolute path, written by
whichever process ingested the PDF — `C:\\Users\\beka\\Desktop\\LuxMedicine\\backend\\storage\\
kdigo_2012_ckd.pdf`. That is not a location, it is a location *on that laptop*. Nothing
noticed until the API ran in a container: the same bytes are mounted at `/app/storage/...`,
`Path('C:\\Users\\...')` does not exist on Linux, and every request for a source PDF returned
500 — the row was fine, only the machine reading it had changed, and that must not matter.

It would have failed the same way on a second developer's machine, on a deploy, or on any
move of the storage directory. The container just made it visible on the first try.

**The fix.** The URI is now relative to `settings.storage_root`, and readers resolve it with
`storage.resolve()` (see app/services/storage.py). This migration rewrites the rows that were
written before that: everything after the root directory's name is the layout and is kept,
the absolute prefix is dropped, and backslashes become forward slashes so a URI written on
Windows resolves on Linux.

Rows whose root is not named `storage` are left alone — `storage.resolve()` still tolerates a
legacy absolute URI, so an unmigrated row is degraded, not broken.

**Not reversible.** The absolute prefix named a directory on a machine this migration cannot
know, so there is nothing to put back; the relative form is what every reader now expects.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # chr(92) rather than a backslash literal: escaping one through Python, alembic and
    # Postgres' string rules is three chances to write a bug into a data migration.
    op.execute(
        """
        UPDATE document_versions
        SET storage_uri = regexp_replace(
            replace(storage_uri, chr(92), '/'),
            '^.*/storage/',
            ''
        )
        WHERE storage_uri ~ '^([A-Za-z]:|/)'
          AND replace(storage_uri, chr(92), '/') ~ '/storage/'
        """
    )


def downgrade() -> None:
    """Nothing to restore — see the module docstring."""
