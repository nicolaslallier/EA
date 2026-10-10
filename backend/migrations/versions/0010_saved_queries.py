"""les requêtes enregistrées : des filtres nommés, partagés

Revision ID: 0010
Revises: 0009
Date: 2026-10-10

`query` est le JSON versionné d'`ElementQuery`. Aucune clé étrangère : un paquet
y est désigné par son identifiant, et sa disparition laisse la requête intacte
— l'exécution répond `unknown_package`, la requête reste modifiable. Le nom est
unique (sensible à la casse) et c'est une contrainte, pas une lecture suivie
d'une insertion.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_queries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_queries")),
        sa.UniqueConstraint("name", name=op.f("uq_saved_queries_name")),
    )


def downgrade() -> None:
    op.drop_table("saved_queries")
