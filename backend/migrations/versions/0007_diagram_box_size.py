"""la taille des boîtes de diagramme

Revision ID: 0007
Revises: 0006
Date: 2026-09-14

Une boîte se redimensionne à la souris : sa largeur et sa hauteur sont
retenues à côté de sa position, dans la même ligne. La valeur par défaut est
la boîte que le diagramme dessinait jusqu'ici (132 x 46), si bien que les
boîtes déjà enregistrées gardent la taille qu'elles avaient à l'écran.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "diagram_nodes", sa.Column("width", sa.Double(), server_default="132", nullable=False)
    )
    op.add_column(
        "diagram_nodes", sa.Column("height", sa.Double(), server_default="46", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("diagram_nodes", "height")
    op.drop_column("diagram_nodes", "width")
