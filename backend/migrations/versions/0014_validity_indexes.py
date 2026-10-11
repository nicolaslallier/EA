"""les dates de validité des éléments et des relations : quatre index d'expression

Revision ID: 0014
Revises: 0013
Date: 2026-10-11

Aucune colonne : `valid_from` et `valid_until` sont des clés de `properties`
(docs/adr/0059), au format `YYYY-MM-DD` de largeur fixe, dont l'ordre
lexicographique est l'ordre chronologique. Cette révision fait deux choses :

1. une migration de données, parce que `properties` était libre : une valeur déjà
   stockée sous ces deux clés est ramenée à son écriture canonique, une valeur vide
   est retirée, et une valeur qui n'est pas un jour est **déplacée** sous
   `legacy_valid_from` / `legacy_valid_until` — rien n'est perdu, rien n'est
   réécrit en silence, et surtout la révision ne **refuse pas** de s'appliquer : une
   migration qui échoue au démarrage du conteneur est une panne de tout le service.
   Les index d'une chaîne mal formée trieraient mal sans rien dire, d'où le déplacement ;
2. la création des quatre index partiels (`WHERE ... IS NOT NULL`).

`downgrade` retire les index ; les dates canonisées et les clés `legacy_*` restent
(elles sont des propriétés ordinaires, lisibles par l'ancien code).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("elements", "relationships")
_KEYS = ("valid_from", "valid_until")
_MAX_DAY_LENGTH = 16


def _canonical(stored: str) -> str | None:
    """The `YYYY-MM-DD` spelling of a stored value, or None when it is not a day."""
    raw = stored.strip()
    if len(raw) > _MAX_DAY_LENGTH:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _rewrite(connection: sa.Connection, table: str, key: str) -> None:
    properties = sa.column("properties", postgresql.JSONB)
    target = sa.table(table, properties)
    stored_value = properties.op("->>")(sa.cast(key, sa.Text))
    stored = connection.execute(
        sa.select(stored_value).select_from(target).where(stored_value.is_not(None)).distinct()
    ).scalars()
    for value in list(stored):
        without = properties.op("-")(sa.cast(key, sa.Text))
        if not value.strip():
            new = without
        elif (canonical := _canonical(value)) is not None:
            if canonical == value:
                continue
            new = sa.func.jsonb_build_object(sa.cast(key, sa.Text), sa.cast(canonical, sa.Text)).op(
                "||"
            )(without)
        else:
            # Not a day: kept, under another name, so that nothing is lost and nothing is indexed.
            new = without.op("||")(
                sa.func.jsonb_build_object(
                    sa.cast(f"legacy_{key}", sa.Text), sa.cast(value, sa.Text)
                )
            )
        connection.execute(sa.update(target).where(stored_value == value).values(properties=new))


def upgrade() -> None:
    connection = op.get_bind()
    for table in _TABLES:
        for key in _KEYS:
            _rewrite(connection, table, key)
    # Du DDL statique : aucune valeur d'exécution.
    for table in _TABLES:
        for key in _KEYS:
            op.create_index(
                f"ix_{table}_{key}",
                table,
                [sa.text(f"(properties ->> '{key}')")],
                postgresql_where=sa.text(f"(properties ->> '{key}') IS NOT NULL"),
            )


def downgrade() -> None:
    for table in _TABLES:
        for key in _KEYS:
            op.drop_index(f"ix_{table}_{key}", table_name=table)
