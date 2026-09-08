"""les passages indexés des documents markdown, et leurs vecteurs

Revision ID: 0003
Revises: 0002
Date: 2026-09-08

La table de la recherche sémantique — voir docs/adr/0019.

Trois points se lisent ici. `CREATE EXTENSION vector` d'abord : pgvector doit
être *installé dans l'image* PostgreSQL, pas seulement activé dans la base ;
sur un serveur qui ne l'a pas, cette migration échoue avec le message de
PostgreSQL, ce qui est exactement le bon endroit pour l'apprendre.

Ensuite la clé étrangère : contrairement à `element_documents.element_id`, qui
désigne un nœud Neo4j et ne peut rien référencer, un passage désigne un
document — une ligne d'à côté. La cascade que le service devait écrire à la
main pour les documents est ici une ligne de DDL.

Enfin l'index HNSW, en SQL brut : Alembic ne rend pas `postgresql_using` avec
ses `postgresql_ops` dans un `create_index` autogénéré. Il est construit après
le `create_table`, sur une table vide, ce qui est le cas rapide.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from ea.domain.search import EMBEDDING_DIMENSIONS

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["element_documents.id"],
            name=op.f("fk_document_chunks_document_id_element_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint(
            "document_id",
            "ordinal",
            name=op.f("uq_document_chunks_document_id_ordinal"),
        ),
    )
    op.create_index(
        op.f("ix_document_chunks_document_id"),
        "document_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_chunks_element_id"),
        "document_chunks",
        ["element_id"],
        unique=False,
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_element_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_document_id"), table_name="document_chunks")
    op.drop_table("document_chunks")
    # L'extension n'est pas supprimée : elle est un objet de la base, pas de
    # cette table, et une autre pourrait déjà s'en servir. `DROP EXTENSION` ici
    # ferait d'un `downgrade` la destruction de quelque chose qu'on n'a pas créé.
