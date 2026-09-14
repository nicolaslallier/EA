"""le graphe d'architecture en tables relationnelles

Revision ID: 0005
Revises: 0004
Date: 2026-09-14

Les éléments et les relations quittent Neo4j — voir docs/adr/0033. Les tables
sont créées vides : les données arrivent par `make graph-import`, et les clés
étrangères des documents et des diagrammes vers `elements` attendent la
révision 0006, qui ne peut être posée qu'une fois le graphe importé.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "elements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("element_type", sa.Text(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("aspect", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("documentation", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_elements")),
        sa.UniqueConstraint("element_type", "name", name=op.f("uq_elements_element_type_name")),
    )
    op.create_index(op.f("ix_elements_element_type"), "elements", ["element_type"], unique=False)
    op.create_index(op.f("ix_elements_layer"), "elements", ["layer"], unique=False)
    # Deux index partiels sur expression : du DDL statique, aucune valeur d'exécution.
    op.create_index(
        "uq_elements_vrf_ip_address",
        "elements",
        [sa.text("(properties ->> 'vrf')"), sa.text("(properties ->> 'ip_address')")],
        unique=True,
        postgresql_where=sa.text("properties ? 'vrf' AND properties ? 'ip_address'"),
    )
    op.create_index(
        "uq_elements_vrf_cidr",
        "elements",
        [sa.text("(properties ->> 'vrf')"), sa.text("(properties ->> 'cidr')")],
        unique=True,
        postgresql_where=sa.text("properties ? 'vrf' AND properties ? 'cidr'"),
    )
    op.create_table(
        "relationships",
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
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["elements.id"],
            name=op.f("fk_relationships_source_id_elements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["elements.id"],
            name=op.f("fk_relationships_target_id_elements"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationships")),
    )
    op.create_index(
        op.f("ix_relationships_source_id"), "relationships", ["source_id"], unique=False
    )
    op.create_index(
        op.f("ix_relationships_target_id"), "relationships", ["target_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_relationships_target_id"), table_name="relationships")
    op.drop_index(op.f("ix_relationships_source_id"), table_name="relationships")
    op.drop_table("relationships")
    op.drop_index("uq_elements_vrf_cidr", table_name="elements")
    op.drop_index("uq_elements_vrf_ip_address", table_name="elements")
    op.drop_index(op.f("ix_elements_layer"), table_name="elements")
    op.drop_index(op.f("ix_elements_element_type"), table_name="elements")
    op.drop_table("elements")
