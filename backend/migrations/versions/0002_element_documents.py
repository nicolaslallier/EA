"""les documents markdown attachés aux éléments d'architecture

Revision ID: 0002
Revises: 0001
Date: 2026-09-07

La première vraie table du socle relationnel — voir docs/adr/0017.

Deux points de conception se lisent ici. `content` est un `TEXT` : le markdown
est de la prose, lue, cherchée et comparée par des humains, et un `bytea`
ferait de chacune de ces opérations un décodage. Et `element_id` ne porte
aucune clé étrangère : l'élément est un nœud Neo4j (docs/adr/0004), PostgreSQL
n'a rien à référencer. La cascade que la clé étrangère aurait faite est donc
explicite, dans `ArchitectureService.delete_element`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "element_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_element_documents")),
        sa.UniqueConstraint(
            "element_id",
            "filename",
            name=op.f("uq_element_documents_element_id_filename"),
        ),
    )
    op.create_index(
        op.f("ix_element_documents_element_id"),
        "element_documents",
        ["element_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_element_documents_element_id"), table_name="element_documents")
    op.drop_table("element_documents")
