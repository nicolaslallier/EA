"""quand un rafraîchissement d'une référence externe a été tenté (docs/adr/0076)

Revision ID: 0021
Revises: 0020
Date: 2026-10-17

`external_references.attempted_at`, nullable : la file de rafraîchissement se classe par
dernière *tentative*, réussie ou non, pour que des lignes durablement périmées ne cachent pas
les suivantes. Une colonne de plus et rien d'autre : aucune ligne existante n'est lue ni
réécrite (une ligne sans tentative se classe par sa dernière synchronisation), la révision
ne peut donc pas échouer sur des données déjà là. Elle n'est pas ajoutée à la `0019`, déjà
appliquée à la base partagée. `lock_timeout` borne l'attente du verrou.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "10s"


def upgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.add_column(
        "external_references",
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.drop_column("external_references", "attempted_at")
