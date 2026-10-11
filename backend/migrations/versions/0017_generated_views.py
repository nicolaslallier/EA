"""les vues générées : source, vivante ou figée

Revision ID: 0017
Revises: 0016
Date: 2026-10-11

Un diagramme peut être « tout ce qui répond à cette requête » ou « tout ce que
ce paquet contient » (docs/adr/0065). Il référence sa source par clé étrangère
`RESTRICT` : supprimer une requête ou un paquet lu par une vue est refusé, jamais une
vue qui change de sens en silence.

Rien à convertir et rien qui puisse échouer sur les données existantes : cinq colonnes
nullables ou à défaut serveur, et trois contraintes `CHECK` que toute ligne existante
satisfait par construction (`generation` et les deux sources valent NULL partout, donc
`(generation is null) = (… is null and … is null)` est vrai). Les diagrammes existants sont
des diagrammes dessinés à la main. `lock_timeout` borne l'attente des verrous.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "10s"


def upgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.add_column("diagrams", sa.Column("generation", sa.String(length=6), nullable=True))
    op.add_column("diagrams", sa.Column("source_query_id", sa.Uuid(), nullable=True))
    op.add_column("diagrams", sa.Column("source_package_id", sa.Uuid(), nullable=True))
    op.add_column(
        "diagrams",
        sa.Column("source_recursive", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_diagrams_source_query_id_saved_queries"),
        "diagrams",
        "saved_queries",
        ["source_query_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_diagrams_source_package_id_packages"),
        "diagrams",
        "packages",
        ["source_package_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_diagrams_source_query_id"), "diagrams", ["source_query_id"])
    op.create_index(op.f("ix_diagrams_source_package_id"), "diagrams", ["source_package_id"])
    op.create_check_constraint(
        op.f("ck_diagrams_generation"), "diagrams", "generation in ('live', 'frozen')"
    )
    op.create_check_constraint(
        op.f("ck_diagrams_one_source"),
        "diagrams",
        "num_nonnulls(source_query_id, source_package_id) <= 1",
    )
    op.create_check_constraint(
        op.f("ck_diagrams_generation_iff_source"),
        "diagrams",
        "(generation is null) = (source_query_id is null and source_package_id is null)",
    )


def downgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.drop_constraint(op.f("ck_diagrams_generation_iff_source"), "diagrams", type_="check")
    op.drop_constraint(op.f("ck_diagrams_one_source"), "diagrams", type_="check")
    op.drop_constraint(op.f("ck_diagrams_generation"), "diagrams", type_="check")
    op.drop_index(op.f("ix_diagrams_source_package_id"), table_name="diagrams")
    op.drop_index(op.f("ix_diagrams_source_query_id"), table_name="diagrams")
    op.drop_constraint(
        op.f("fk_diagrams_source_package_id_packages"), "diagrams", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_diagrams_source_query_id_saved_queries"), "diagrams", type_="foreignkey"
    )
    op.drop_column("diagrams", "source_recursive")
    op.drop_column("diagrams", "source_package_id")
    op.drop_column("diagrams", "source_query_id")
    op.drop_column("diagrams", "generation")
