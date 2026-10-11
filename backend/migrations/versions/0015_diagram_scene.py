"""les objets de vue, les tracés de liens et la révision d'un diagramme

Revision ID: 0015
Revises: 0014
Date: 2026-10-11

Notes et groupes (`diagram_objects`, une seule table : même géométrie, même
empilement, même imbrication), tracés de liens (`diagram_edges`), parent et
ordre d'empilement d'une boîte, et `diagrams.revision` — le compteur qui permet
de refuser un enregistrement fait sur une copie périmée (docs/adr/0063).

Rien à convertir : tout est ajout nullable ou défaut serveur, les diagrammes existants
gardent leurs boîtes à l'identique, aucune valeur n'est lue ni transformée - la révision
ne peut donc pas échouer sur des données existantes. `lock_timeout` borne l'attente des
verrous.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LOCK_TIMEOUT = "10s"


def upgrade() -> None:
    # Waiting for a lock is bounded: a migration that hangs at boot is an API that is down.
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.add_column(
        "diagrams", sa.Column("revision", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_table(
        "diagram_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("diagram_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("x", sa.Double(), nullable=False),
        sa.Column("y", sa.Double(), nullable=False),
        sa.Column("width", sa.Double(), nullable=False),
        sa.Column("height", sa.Double(), nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("colour", sa.String(length=7), nullable=True),
        sa.Column("z", sa.Integer(), server_default="0", nullable=False),
        sa.Column("anchor_element_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("kind in ('note', 'group')", name=op.f("ck_diagram_objects_kind")),
        sa.CheckConstraint(
            "kind = 'note' or anchor_element_id is null",
            name=op.f("ck_diagram_objects_group_has_no_anchor"),
        ),
        sa.ForeignKeyConstraint(
            ["diagram_id"],
            ["diagrams.id"],
            name=op.f("fk_diagram_objects_diagram_id_diagrams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["diagram_objects.id"],
            name=op.f("fk_diagram_objects_parent_id_diagram_objects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_element_id"],
            ["elements.id"],
            name=op.f("fk_diagram_objects_anchor_element_id_elements"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagram_objects")),
    )
    op.create_index(
        op.f("ix_diagram_objects_diagram_id"), "diagram_objects", ["diagram_id"], unique=False
    )
    op.create_index(
        op.f("ix_diagram_objects_anchor_element_id"),
        "diagram_objects",
        ["anchor_element_id"],
        unique=False,
    )
    op.create_table(
        "diagram_edges",
        sa.Column("diagram_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_id", sa.Uuid(), nullable=False),
        sa.Column(
            "waypoints",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["diagram_id"],
            ["diagrams.id"],
            name=op.f("fk_diagram_edges_diagram_id_diagrams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["relationships.id"],
            name=op.f("fk_diagram_edges_relationship_id_relationships"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("diagram_id", "relationship_id", name=op.f("pk_diagram_edges")),
    )
    op.create_index(
        op.f("ix_diagram_edges_relationship_id"),
        "diagram_edges",
        ["relationship_id"],
        unique=False,
    )
    op.add_column("diagram_nodes", sa.Column("parent_object_id", sa.Uuid(), nullable=True))
    op.add_column("diagram_nodes", sa.Column("z", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key(
        op.f("fk_diagram_nodes_parent_object_id_diagram_objects"),
        "diagram_nodes",
        "diagram_objects",
        ["parent_object_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_diagram_nodes_parent_object_id"),
        "diagram_nodes",
        ["parent_object_id"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.drop_index(op.f("ix_diagram_nodes_parent_object_id"), table_name="diagram_nodes")
    op.drop_constraint(
        op.f("fk_diagram_nodes_parent_object_id_diagram_objects"),
        "diagram_nodes",
        type_="foreignkey",
    )
    op.drop_column("diagram_nodes", "z")
    op.drop_column("diagram_nodes", "parent_object_id")
    op.drop_index(op.f("ix_diagram_edges_relationship_id"), table_name="diagram_edges")
    op.drop_table("diagram_edges")
    op.drop_index(op.f("ix_diagram_objects_anchor_element_id"), table_name="diagram_objects")
    op.drop_index(op.f("ix_diagram_objects_diagram_id"), table_name="diagram_objects")
    op.drop_table("diagram_objects")
    op.drop_column("diagrams", "revision")
