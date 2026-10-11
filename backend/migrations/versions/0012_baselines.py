"""les baselines : une copie normalisée du graphe

Revision ID: 0012
Revises: 0011
Date: 2026-10-10

Une baseline recopie les colonnes de `elements`, `relationships` et des boîtes
de diagramme sous un `baseline_id`. Aucune clé vers le modèle vivant — ni vers
`elements`, ni vers `packages`, ni vers `diagrams` : la copie doit survivre à ce
qu'elle a copié. Le paquet d'un élément est gardé par son id et son chemin
lisible, sans clé. Les documents ne sont pas capturés (docs/adr/0047).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _baseline_id(table: str) -> sa.Column[UUID]:
    return sa.Column(
        "baseline_id",
        sa.Uuid(),
        sa.ForeignKey(
            "baselines.id",
            name=op.f(f"fk_{table}_baseline_id_baselines"),
            ondelete="CASCADE",
        ),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "baselines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_by_sub", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_baselines")),
        sa.UniqueConstraint("name", name=op.f("uq_baselines_name")),
    )
    op.create_table(
        "baseline_elements",
        _baseline_id("baseline_elements"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("element_type", sa.Text(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("aspect", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("documentation", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("package_id", sa.Uuid(), nullable=True),
        sa.Column("package_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("baseline_id", "id", name=op.f("pk_baseline_elements")),
    )
    op.create_table(
        "baseline_relationships",
        _baseline_id("baseline_relationships"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), server_default="", nullable=False),
        sa.Column("access_type", sa.Text(), nullable=True),
        sa.Column("directed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("baseline_id", "id", name=op.f("pk_baseline_relationships")),
    )
    op.create_table(
        "baseline_boxes",
        _baseline_id("baseline_boxes"),
        sa.Column("diagram_id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("x", sa.Double(), nullable=False),
        sa.Column("y", sa.Double(), nullable=False),
        sa.Column("width", sa.Double(), nullable=False),
        sa.Column("height", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint(
            "baseline_id", "diagram_id", "element_id", name=op.f("pk_baseline_boxes")
        ),
    )


def downgrade() -> None:
    op.drop_table("baseline_boxes")
    op.drop_table("baseline_relationships")
    op.drop_table("baseline_elements")
    op.drop_table("baselines")
