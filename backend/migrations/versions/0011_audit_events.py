"""le journal d'audit

Revision ID: 0011
Revises: 0010
Date: 2026-10-10

Une table append-only: ce que le catalogue sait de ses propres changements.
`entity_id` n'a aucune clé étrangère, volontairement — l'événement doit survivre
à l'élément qu'il décrit. L'immuabilité est un trigger (`UPDATE`, `DELETE` et
`TRUNCATE` lèvent), parce que le déploiement n'a qu'un rôle propriétaire `ea`
(docs/adr/0029) : un `REVOKE` ne lierait personne. Le rôle d'écriture
`INSERT, SELECT` seul est à provisionner hors de ce repo.

Le rétro-remplissage donne à chaque élément et chaque lien existant un premier
événement (`ea-system`, `correlation = 'backfill'`), pour que l'historique ne
commence pas par un trou. Les diagrammes, les documents, les paquets et les
requêtes enregistrées n'en reçoivent pas. Rejouer le rétro-remplissage ne
double rien (`NOT EXISTS`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# DDL statique, aucune valeur d'exécution : rien à lier.
_FUNCTION = """
CREATE FUNCTION audit_events_are_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = 'restrict_violation';
END
$$
"""
_ROW_TRIGGER = """
CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_are_append_only()
"""
_TRUNCATE_TRIGGER = """
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
FOR EACH STATEMENT EXECUTE FUNCTION audit_events_are_append_only()
"""

_BACKFILL_ELEMENTS = """
INSERT INTO audit_events
    (id, at, actor_sub, actor_name, action, entity_kind, entity_id, before, after,
     request_id, channel, correlation)
SELECT gen_random_uuid(), e.created_at, 'ea-system', 'ea-system', 'element.created', 'element',
       e.id, NULL,
       jsonb_build_object(
           'id', CAST(e.id AS text), 'element_type', e.element_type, 'layer', e.layer,
           'aspect', e.aspect, 'name', e.name, 'description', e.description,
           'documentation', e.documentation,
           'package_id', CAST(e.package_id AS text), 'properties', e.properties,
           'created_at', to_jsonb(e.created_at), 'updated_at', to_jsonb(e.updated_at)),
       '-', 'system', 'backfill'
FROM elements e
WHERE NOT EXISTS (
    SELECT 1 FROM audit_events a
    WHERE a.entity_id = e.id AND a.correlation = 'backfill' AND a.action = 'element.created')
"""
_BACKFILL_RELATIONSHIPS = """
INSERT INTO audit_events
    (id, at, actor_sub, actor_name, action, entity_kind, entity_id, before, after,
     request_id, channel, correlation)
SELECT gen_random_uuid(), r.created_at, 'ea-system', 'ea-system', 'relationship.created',
       'relationship', r.id, NULL,
       jsonb_build_object(
           'id', CAST(r.id AS text), 'relationship_type', r.relationship_type,
           'source_id', CAST(r.source_id AS text), 'target_id', CAST(r.target_id AS text),
           'source_type', r.source_type, 'target_type', r.target_type, 'name', r.name,
           'access_type', r.access_type, 'directed', r.directed, 'properties', r.properties,
           'created_at', to_jsonb(r.created_at)),
       '-', 'system', 'backfill'
FROM relationships r
WHERE NOT EXISTS (
    SELECT 1 FROM audit_events a
    WHERE a.entity_id = r.id AND a.correlation = 'backfill'
      AND a.action = 'relationship.created')
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_sub", sa.Text(), nullable=False),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), server_default="system", nullable=False),
        sa.Column("correlation", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["entity_kind", "entity_id", sa.text("at DESC")],
    )
    op.create_index("ix_audit_events_at", "audit_events", [sa.text("at DESC")])
    op.create_index("ix_audit_events_actor_sub", "audit_events", ["actor_sub", sa.text("at DESC")])
    # Le rétro-remplissage d'abord : le trigger ne gêne pas un INSERT, mais une
    # migration qui se corrige elle-même doit pouvoir le faire avant d'être scellée.
    op.execute(_BACKFILL_ELEMENTS)
    op.execute(_BACKFILL_RELATIONSHIPS)
    op.execute(_FUNCTION)
    op.execute(_ROW_TRIGGER)
    op.execute(_TRUNCATE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION audit_events_are_append_only()")
    op.drop_index("ix_audit_events_actor_sub", table_name="audit_events")
    op.drop_index("ix_audit_events_at", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_table("audit_events")
