"""les paquets : un arbre de classement pour les éléments et les diagrammes

Revision ID: 0009
Revises: 0008
Date: 2026-10-10

`packages` est une liste d'adjacence (`parent_id`). `elements.package_id` et
`diagrams.package_id` sont nullables : tout ce qui existe déjà reste « à la
racine », aucune donnée n'est réécrite. Toutes les clés sont `RESTRICT` — la
suppression d'un paquet ne supprime ni ne déplace jamais son contenu, le
service décide et le dit.

Les noms de frères sont uniques sans tenir compte de la casse, ce qui demande
deux index partiels (racine / enfants) plutôt qu'un `NULLS NOT DISTINCT`,
lequel dépendrait de la version de PostgreSQL de l'Infra.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["packages.id"],
            name=op.f("fk_packages_parent_id_packages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packages")),
    )
    # DDL statique, aucune valeur d'exécution.
    op.create_index(
        "uq_packages_root_name",
        "packages",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "uq_packages_child_name",
        "packages",
        ["parent_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    for table in ("elements", "diagrams"):
        op.add_column(table, sa.Column("package_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{table}_package_id_packages"),
            table,
            "packages",
            ["package_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(op.f(f"ix_{table}_package_id"), table, ["package_id"], unique=False)


def downgrade() -> None:
    for table in ("diagrams", "elements"):
        op.drop_index(op.f(f"ix_{table}_package_id"), table_name=table)
        op.drop_constraint(op.f(f"fk_{table}_package_id_packages"), table, type_="foreignkey")
        op.drop_column(table, "package_id")
    op.drop_index("uq_packages_child_name", table_name="packages")
    op.drop_index("uq_packages_root_name", table_name="packages")
    op.drop_table("packages")
