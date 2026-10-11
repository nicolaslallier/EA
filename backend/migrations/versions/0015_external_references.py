"""les références externes d'un élément (docs/adr/0062)

Revision ID: 0015
Revises: 0014
Date: 2026-10-11

Une table neuve et rien d'autre : aucune donnée existante n'est lue, réécrite ni
convertie, donc cette révision ne peut pas échouer sur des lignes déjà là (elle tourne au
démarrage du conteneur). Aucun index d'expression, aucun CAST. `lock_timeout` borne
l'attente du verrou que prend la clé étrangère sur `elements` (un `ALTER`-like bref).
`downgrade` supprime la table : les liens vers des outils tiers sont perdus, rien d'autre
(les objets externes ne sont jamais touchés).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "10s"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")  # nosec B608 - a constant
    op.create_table(
        "external_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=32), server_default="", nullable=False),
        sa.Column("external_id", sa.String(length=2048), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=500), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="unknown", nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind <> ''", name=op.f("ck_external_references_kind_not_empty")),
        sa.CheckConstraint(
            "status in ('open', 'closed', 'unknown')",
            name=op.f("ck_external_references_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["element_id"],
            ["elements.id"],
            name=op.f("fk_external_references_element_id_elements"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_references")),
        sa.UniqueConstraint(
            "element_id",
            "target",
            "external_id",
            name=op.f("uq_external_references_element_id_target_external_id"),
        ),
    )
    op.create_index(
        op.f("ix_external_references_element_id"),
        "external_references",
        ["element_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_external_references_element_id"), table_name="external_references")
    op.drop_table("external_references")
