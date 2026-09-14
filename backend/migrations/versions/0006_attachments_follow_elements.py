"""les documents et les boîtes de diagramme suivent leur élément

Revision ID: 0006
Revises: 0005
Date: 2026-09-14

L'élément est une ligne depuis 0005 (docs/adr/0033) : `element_documents` et
`diagram_nodes` peuvent enfin le désigner par une clé étrangère, et la cascade
explicite de `ArchitectureService.delete_element` disparaît.

Deux étapes précèdent les clés. La première **refuse** de tourner si `elements`
est vide alors que des documents ou des boîtes existent : l'image applique
`alembic upgrade head` à chaque démarrage, et sans ce refus un déploiement
arrivé avant `make graph-import` prendrait chaque attachement pour un orphelin
et l'effacerait. La seconde supprime les vrais orphelins — ceux que l'ADR 0017
acceptait quand la suppression d'un élément ne pouvait pas atteindre ses
documents — sans quoi la clé ne pourrait pas être posée.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ATTACHED = ("element_documents", "diagram_nodes")


def upgrade() -> None:
    bind = op.get_bind()
    elements = sa.table("elements", sa.column("id"))
    has_elements = bind.scalar(sa.select(sa.exists().select_from(elements)))
    attached = [sa.table(name, sa.column("element_id")) for name in _ATTACHED]
    has_attachments = any(
        bind.scalar(sa.select(sa.exists().select_from(table))) for table in attached
    )
    if not has_elements and has_attachments:
        msg = (
            "la table elements est vide alors que des documents ou des diagrammes existent : "
            "importe d'abord le graphe (make graph-import, docs/adr/0033)"
        )
        raise RuntimeError(msg)

    for table in attached:
        purged = bind.execute(
            sa.delete(table).where(~sa.exists().where(elements.c.id == table.c.element_id))
        ).rowcount
        logger.warning("%s : %d ligne(s) orpheline(s) supprimée(s)", table.name, purged)

    op.create_foreign_key(
        op.f("fk_element_documents_element_id_elements"),
        "element_documents",
        "elements",
        ["element_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_diagram_nodes_element_id_elements"),
        "diagram_nodes",
        "elements",
        ["element_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Les lignes supprimées comme orphelines ne reviennent pas.
    op.drop_constraint(
        op.f("fk_diagram_nodes_element_id_elements"), "diagram_nodes", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_element_documents_element_id_elements"), "element_documents", type_="foreignkey"
    )
