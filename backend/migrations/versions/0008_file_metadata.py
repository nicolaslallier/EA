"""les métadonnées des fichiers déposés dans MinIO

Revision ID: 0008
Revises: 0007
Date: 2026-09-16

Les octets restent dans le bucket ; cette table dit ce que le bucket ne sait
pas — qui a déposé un fichier, quand ce catalogue l'a vu pour la première
fois, ce qu'une personne en a écrit, l'empreinte de son contenu — et recopie
ce qu'il sait, pour qu'un listage d'un millier de fichiers coûte une requête
et non mille `stat`. Voir docs/adr/0039.

Aucune clé étrangère : l'autre bout de la relation est un objet MinIO, que
PostgreSQL n'a rien à référencer. C'est le rapprochement (`ea.files_reindex`)
qui retire une ligne dont l'objet a disparu, jamais une cascade.

`sha256` est nullable à dessein : l'empreinte est connue d'un fichier reçu par
cette API, inconnue d'un fichier écrit par le pipeline ou la console MinIO. Une
chaîne vide dirait « calculée, et vide », ce qui est l'empreinte d'aucun octet
— un autre fait.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_metadata",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("etag", sa.Text(), server_default="", nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("last_modified", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "tags",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("uploaded_by_subject", sa.Text(), server_default="", nullable=False),
        sa.Column("uploaded_by", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_metadata")),
        sa.UniqueConstraint("object_key", name=op.f("uq_file_metadata_object_key")),
    )
    # « le même fichier sous un autre nom ? » est la seule question que
    # l'empreinte sert à poser, et elle la pose par cet index.
    op.create_index(op.f("ix_file_metadata_sha256"), "file_metadata", ["sha256"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_file_metadata_sha256"), table_name="file_metadata")
    op.drop_table("file_metadata")
