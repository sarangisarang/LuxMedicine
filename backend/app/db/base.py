from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Imported for their side effect: Alembic's autogenerate needs every model
# registered on Base.metadata before it diffs against the live database.
from app.models import (  # noqa: E402,F401
    alias,
    audit,
    checkpoint,
    chunk,
    document,
    erasure,
    invite,
    query,
)
