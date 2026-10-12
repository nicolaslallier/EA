"""les profils, les stéréotypes et leur application aux éléments

Revision ID: 0016
Revises: 0015
Date: 2026-10-11

Donnée de référence : un stéréotype est une étiquette posée sur un élément d'un
type de base, jamais un type nouveau (docs/adr/0070). `base_type` est une
chaîne contrôlée par le domaine — un enum PostgreSQL figerait le métamodèle dans
le schéma. `profile_properties.position` garde l'ordre de déclaration. Rien à
convertir : quatre tables neuves, aucune lecture ni transformation de donnée existante -
la révision ne peut pas échouer sur le contenu de la base. `lock_timeout` borne l'attente des
verrous (les clés étrangères vers `elements` en prennent un).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LOCK_TIMEOUT = "10s"


def upgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
        sa.UniqueConstraint("name", name=op.f("uq_profiles_name")),
    )
    op.create_table(
        "profile_stereotypes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_type", sa.String(length=64), nullable=False),
        sa.Column("colour", sa.String(length=7), nullable=True),
        sa.Column("icon_key", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_profile_stereotypes_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_stereotypes")),
        sa.UniqueConstraint(
            "profile_id", "name", name=op.f("uq_profile_stereotypes_profile_id_name")
        ),
    )
    op.create_table(
        "profile_properties",
        sa.Column("stereotype_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=63), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column(
            "allowed_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("template", sa.Text(), nullable=True),
        sa.Column("help", sa.Text(), server_default="", nullable=False),
        sa.ForeignKeyConstraint(
            ["stereotype_id"],
            ["profile_stereotypes.id"],
            name=op.f("fk_profile_properties_stereotype_id_profile_stereotypes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("stereotype_id", "key", name=op.f("pk_profile_properties")),
    )
    op.create_table(
        "element_stereotypes",
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("stereotype_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["element_id"],
            ["elements.id"],
            name=op.f("fk_element_stereotypes_element_id_elements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stereotype_id"],
            ["profile_stereotypes.id"],
            name=op.f("fk_element_stereotypes_stereotype_id_profile_stereotypes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("element_id", "stereotype_id", name=op.f("pk_element_stereotypes")),
    )
    op.create_index(
        op.f("ix_element_stereotypes_stereotype_id"),
        "element_stereotypes",
        ["stereotype_id"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.drop_index(op.f("ix_element_stereotypes_stereotype_id"), table_name="element_stereotypes")
    op.drop_table("element_stereotypes")
    op.drop_table("profile_properties")
    op.drop_table("profile_stereotypes")
    op.drop_table("profiles")
