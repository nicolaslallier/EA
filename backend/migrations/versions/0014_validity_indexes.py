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
   `legacy_valid_from` / `legacy_valid_until` — rien n'est perdu, et surtout la
   révision ne **refuse pas** de s'appliquer : une migration qui échoue au démarrage
   du conteneur est une panne de tout le service. Aucune valeur n'est convertie en
   SQL (pas de `CAST`) : la lecture se fait en Python, sur les seules valeurs
   **distinctes de 16 caractères au plus** ; une valeur plus longue n'est jamais
   rapatriée, elle est déplacée par une instruction SQL. Chaque (table, clé) coûte
   trois passes sur la table, quel que soit le nombre de valeurs distinctes ;
2. la création des quatre index partiels (`WHERE ... IS NOT NULL`) sur
   `properties ->> clé`, une expression totale et immutable (aucun cast : elle ne
   peut lever sur aucune ligne, présente ou future). `CREATE INDEX` sans
   `CONCURRENTLY` verrouille l'écriture de la table le temps de la construction :
   `lock_timeout` borne l'attente du verrou (pas la construction, de l'ordre de la
   seconde pour quelques dizaines de milliers de lignes).

Tout tient dans la transaction de la révision : elle réussit entière ou ne laisse rien.
`downgrade` retire les index (idempotent) ; les dates canonisées et les clés `legacy_*`
restent, ce sont des propriétés ordinaires.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("elements", "relationships")
_KEYS = ("valid_from", "valid_until")
_MAX_DAY_LENGTH = 16
_LOCK_TIMEOUT = "10s"
_LEGACY_SLOTS = 9


def _canonical(stored: str) -> str | None:
    """The `YYYY-MM-DD` spelling of a stored value, or None when it is not a day."""
    try:
        return date.fromisoformat(stored.strip()).isoformat()
    except ValueError:
        return None


def _aside(properties: str, key: str, value: str) -> str:
    """SQL for `jsonb_build_object(<free name>, <value>)`: never overwrites.

    The name is `legacy_<key>`, else `legacy_<key>_2` ... `_9`, the first not already a key of the
    row. Only when all nine are taken is the value appended (after a newline) to the last one -
    nothing is lost. Every identifier here is built from the two constants above, never from data.
    """
    value = f"({value})"
    names = [f"legacy_{key}"] + [f"legacy_{key}_{n}" for n in range(2, _LEGACY_SLOTS + 1)]
    name = (
        "CASE "
        + " ".join(f"WHEN NOT ({properties} ? '{n}') THEN '{n}'" for n in names)
        + f" ELSE '{names[-1]}' END"
    )
    all_taken = " AND ".join(f"({properties} ? '{n}')" for n in names)
    stored = (
        f"CASE WHEN {all_taken} THEN ({properties} ->> '{names[-1]}') || chr(10) || {value} "
        f"ELSE {value} END"
    )
    return f"jsonb_build_object({name}, {stored})"


def _rewrite(connection: sa.Connection, table: str, key: str) -> None:
    # `table` and `key` come from the two constants above; every value is a bound parameter.
    value = f"(properties ->> '{key}')"
    # 1. What is too long to be a day: set aside by SQL alone, never read into Python.
    connection.execute(
        sa.text(
            f"UPDATE {table} SET properties = (properties - '{key}') "  # nosec B608 - constants
            f"|| {_aside('properties', key, value)} "
            f"WHERE length({value}) > :longest"
        ),
        {"longest": _MAX_DAY_LENGTH},
    )
    # 2. The distinct short values: at most 16 characters each, so cheap to read.
    shorts = (
        connection.execute(
            sa.text(
                f"SELECT DISTINCT {value} FROM {table} WHERE {value} IS NOT NULL"  # nosec B608 - constants
            )
        )
        .scalars()
        .all()
    )
    olds: list[str] = []
    kinds: list[str] = []
    news: list[str] = []
    for stored in shorts:
        canonical = _canonical(stored)
        if not stored.strip():
            kind, new = "blank", ""
        elif canonical is None:
            kind, new = "legacy", ""
        elif canonical == stored:
            continue
        else:
            kind, new = "canonical", canonical
        olds.append(stored)
        kinds.append(kind)
        news.append(new)
    if not olds:
        return
    # 3. One pass: each row meets its verdict through a join on the stored value.
    connection.execute(
        sa.text(
            f"UPDATE {table} AS t SET properties = CASE m.kind "  # nosec B608 - constants
            f"WHEN 'blank' THEN t.properties - '{key}' "
            f"WHEN 'canonical' THEN (t.properties - '{key}') "
            f"|| jsonb_build_object('{key}', m.new) "
            f"ELSE (t.properties - '{key}') "
            f"|| {_aside('t.properties', key, f't.properties ->> {chr(39)}{key}{chr(39)}')} END "
            f"FROM unnest(CAST(:olds AS text[]), CAST(:kinds AS text[]), CAST(:news AS text[])) "
            f"AS m(old, kind, new) WHERE (t.properties ->> '{key}') = m.old"
        ),
        {"olds": olds, "kinds": kinds, "news": news},
    )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
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
            op.drop_index(f"ix_{table}_{key}", table_name=table, if_exists=True)
