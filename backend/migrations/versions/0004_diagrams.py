"""les diagrammes enregistrés, vues sur le graphe d'architecture

Revision ID: 0004
Revises: 0003
Date: 2026-09-13

Les deux tables du constructeur de diagrammes — voir docs/adr/0031.

Un diagramme est une *vue* ArchiMate : il ne possède aucun fait, il retient
seulement quels éléments sont dessinés et où. `diagram_nodes.element_id` ne
porte donc aucune clé étrangère, pour la même raison que
`element_documents.element_id` : l'élément est un nœud Neo4j, et sa cascade est
explicite, dans `ArchitectureService.delete_element`. `diagram_id`, lui, désigne
une ligne d'à côté : cette cascade-là est une ligne de DDL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagrams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagrams")),
        sa.UniqueConstraint("name", name=op.f("uq_diagrams_name")),
    )
    op.create_table(
        "diagram_nodes",
        sa.Column("diagram_id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("x", sa.Double(), nullable=False),
        sa.Column("y", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["diagram_id"],
            ["diagrams.id"],
            name=op.f("fk_diagram_nodes_diagram_id_diagrams"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("diagram_id", "element_id", name=op.f("pk_diagram_nodes")),
    )
    op.create_index(
        op.f("ix_diagram_nodes_element_id"),
        "diagram_nodes",
        ["element_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_diagram_nodes_element_id"), table_name="diagram_nodes")
    op.drop_table("diagram_nodes")
    op.drop_table("diagrams")
