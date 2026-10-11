"""les modèles de diagrammes

Revision ID: 0018
Revises: 0017
Date: 2026-10-11

Un modèle est un squelette typé - des emplacements, des groupes, des notes, des liens par
type de relation - sans aucun élément (docs/adr/0066). Son corps est un `jsonb` versionné
qu'on n'interroge jamais à l'intérieur : il est validé en domaine, relu en entier. Une table
neuve, aucune lecture ni transformation de donnée existante : la révision ne peut pas
échouer sur le contenu de la base.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "10s"


def upgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.create_table(
        "diagram_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagram_templates")),
        sa.UniqueConstraint("name", name=op.f("uq_diagram_templates_name")),
    )


def downgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.drop_table("diagram_templates")
