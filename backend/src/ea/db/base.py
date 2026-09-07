"""The declarative base every mapped table inherits from.

Kept apart from `postgres.py` so that importing the metadata — which Alembic's
`env.py` does on every command — never builds an engine or reads a credential.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

#: PostgreSQL names an unnamed constraint itself, and the name it invents is
#: not reproducible across backends or versions. Alembic then writes an
#: `upgrade` it cannot reverse: the generated `downgrade` has no handle to drop.
#: Fixing the pattern here makes every future migration reversible by
#: construction, and it has to be set before the first table exists — changing
#: it afterwards renames constraints already in a deployed database.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The root of the relational mapping. No table inherits from it yet.

    Adding one means a module under `ea.db.models`, imported by that package's
    `__init__` — Alembic autogenerates from `Base.metadata`, and a model no one
    imported is a model it silently omits.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
