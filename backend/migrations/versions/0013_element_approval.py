"""l'état d'approbation et le verrou d'un élément

Revision ID: 0013
Revises: 0012
Date: 2026-10-11

Cinq colonnes sur `elements` (dont `revision`, la version du contenu qu'une
approbation nomme), hors de `properties` : ni l'unicité (type, nom)
ni les index IPAM ne sont touchés, et un `PATCH` qui remplace `properties` ne
peut pas effacer l'état. Les éléments existants deviennent des brouillons non
verrouillés (les valeurs par défaut du serveur : aucune ligne n'est réécrite
par du code) — aucune approbation rétroactive n'est inventée (docs/adr/0046).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "elements",
        sa.Column("approval_state", sa.Text(), server_default="draft", nullable=False),
    )
    op.add_column(
        "elements",
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("elements", sa.Column("approved_by_sub", sa.Text(), nullable=True))
    op.add_column("elements", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "elements",
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_elements_approval_state_known"),
        "elements",
        "approval_state IN ('draft', 'in_review', 'approved')",
    )
    op.create_check_constraint(
        op.f("ck_elements_locked_implies_approved"),
        "elements",
        "NOT locked OR approval_state = 'approved'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_elements_locked_implies_approved"), "elements", type_="check")
    op.drop_constraint(op.f("ck_elements_approval_state_known"), "elements", type_="check")
    op.drop_column("elements", "revision")
    op.drop_column("elements", "approved_at")
    op.drop_column("elements", "approved_by_sub")
    op.drop_column("elements", "locked")
    op.drop_column("elements", "approval_state")
