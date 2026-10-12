"""les versions d'un package et le gel de son sous-arbre (docs/adr/0074)

Revision ID: 0020
Revises: 0019
Date: 2026-10-12

Une table neuve, deux colonnes nullables sur `baselines`, des fonctions et des triggers :
aucune donnée existante n'est lue, réécrite ni convertie, et tant qu'aucune version n'est
en vigueur les triggers sortent à la première sonde (un index partiel vide) : cette révision
ne peut pas échouer sur des lignes déjà là. `lock_timeout` borne l'attente des verrous que
prend la création des triggers. `downgrade` retire triggers, fonctions et table : les versions
publiées sont perdues, les baselines qu'elles désignent restent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "10s"

# DDL statique, aucune valeur d'exécution : rien à lier.
_FROZEN_ANCESTOR = """
CREATE FUNCTION ea_frozen_ancestor(start uuid)
RETURNS TABLE (package_id uuid, version text)
LANGUAGE sql STABLE SET search_path = public, pg_temp AS $$
    WITH RECURSIVE up (id, parent_id, depth) AS (
        SELECT p.id, p.parent_id, 0 FROM packages p WHERE p.id = start
        UNION ALL
        SELECT p.id, p.parent_id, up.depth + 1 FROM packages p JOIN up ON p.id = up.parent_id
        WHERE up.depth < 64
    )
    SELECT v.package_id, v.version
    FROM up JOIN package_versions v ON v.package_id = up.id AND v.reopened_at IS NULL
    ORDER BY up.depth
    LIMIT 1
$$
"""

_ASSERT_UNFROZEN = """
CREATE FUNCTION ea_assert_unfrozen(pkg uuid, tbl text, op text) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = public, pg_temp AS $$
DECLARE
    hit record;
BEGIN
    IF pkg IS NULL THEN
        RETURN;
    END IF;
    -- The common case, nothing published anywhere: one probe of the partial index.
    IF NOT EXISTS (SELECT 1 FROM package_versions WHERE reopened_at IS NULL) THEN
        RETURN;
    END IF;
    SELECT * INTO hit FROM ea_frozen_ancestor(pkg);
    IF FOUND THEN
        RAISE EXCEPTION 'package is published: reopen it first'
            USING ERRCODE = 'EA001',
                  DETAIL = hit.package_id::text || '|' || hit.version || '|' || tbl || '|' || op;
    END IF;
END
$$
"""

_RETURN_ROW = """
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
"""


# Nothing published anywhere: leave before any owner sub-select is evaluated.
_PROBE = """
    IF NOT EXISTS (SELECT 1 FROM package_versions WHERE reopened_at IS NULL) THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
"""


def _guard(name: str, body: str) -> str:
    return (
        f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql "
        f"SET search_path = public, pg_temp AS $$\n"
        f"BEGIN\n{_PROBE}{body}{_RETURN_ROW}END\n$$"
    )


# Both the old and the new place of a row are checked: filing out of, or into, a published
# subtree is a write to it. Elements and diagrams both carry their own `package_id`.
_BY_OWN_PACKAGE = """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(OLD.package_id, TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(NEW.package_id, TG_TABLE_NAME, TG_OP);
    END IF;
"""
_ELEMENTS = _guard("ea_guard_elements", _BY_OWN_PACKAGE)
_DIAGRAMS = _guard("ea_guard_diagrams", _BY_OWN_PACKAGE)
_PACKAGES = _guard(
    "ea_guard_packages",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(OLD.id, TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(NEW.parent_id, TG_TABLE_NAME, TG_OP);
    END IF;
""",
)
_RELATIONSHIPS = _guard(
    "ea_guard_relationships",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = OLD.source_id), TG_TABLE_NAME, TG_OP);
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = OLD.target_id), TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = NEW.source_id), TG_TABLE_NAME, TG_OP);
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = NEW.target_id), TG_TABLE_NAME, TG_OP);
    END IF;
""",
)
# A row that hangs from an element (a document, a stereotype) or from a diagram (a box, a note,
# a route): the owner's place decides.
# Direct field access, so a renamed column fails loudly, not silently.
_OF_ELEMENT = _guard(
    "ea_guard_of_element",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = OLD.element_id),
            TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = NEW.element_id),
            TG_TABLE_NAME, TG_OP);
    END IF;
""",
)
_OF_DIAGRAM = _guard(
    "ea_guard_of_diagram",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM diagrams WHERE id = OLD.diagram_id),
            TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM diagrams WHERE id = NEW.diagram_id),
            TG_TABLE_NAME, TG_OP);
    END IF;
""",
)

_VERSIONS_IMMUTABLE = """
CREATE FUNCTION ea_package_versions_append_only() RETURNS trigger LANGUAGE plpgsql
SET search_path = public, pg_temp AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'package_versions is append-only' USING ERRCODE = 'restrict_violation';
    END IF;
    -- Only closing a version in force is allowed, and nothing else may move with it.
    IF OLD.reopened_at IS NOT NULL
       OR NEW.reopened_at IS NULL
       OR (NEW.id, NEW.package_id, NEW.version, NEW.bump, NEW.notes, NEW.adr, NEW.baseline_id,
           NEW.element_count, NEW.relationship_count, NEW.box_count, NEW.published_by_sub,
           NEW.published_by, NEW.published_at, NEW.publish_audit_event_id)
          IS DISTINCT FROM
          (OLD.id, OLD.package_id, OLD.version, OLD.bump, OLD.notes, OLD.adr, OLD.baseline_id,
           OLD.element_count, OLD.relationship_count, OLD.box_count, OLD.published_by_sub,
           OLD.published_by, OLD.published_at, OLD.publish_audit_event_id)
    THEN
        RAISE EXCEPTION 'package_versions is append-only' USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$
"""

#: (table, trigger function) — one BEFORE ... FOR EACH ROW trigger each.
_GUARDED = (
    ("elements", "ea_guard_elements"),
    ("relationships", "ea_guard_relationships"),
    ("element_documents", "ea_guard_of_element"),
    ("element_stereotypes", "ea_guard_of_element"),
    ("diagrams", "ea_guard_diagrams"),
    ("diagram_nodes", "ea_guard_of_diagram"),
    ("diagram_objects", "ea_guard_of_diagram"),
    ("diagram_edges", "ea_guard_of_diagram"),
    ("packages", "ea_guard_packages"),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")  # nosec B608 - a constant
    op.create_table(
        "package_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("bump", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("adr", sa.Text(), nullable=True),
        sa.Column("baseline_id", sa.Uuid(), nullable=False),
        sa.Column("element_count", sa.Integer(), nullable=False),
        sa.Column("relationship_count", sa.Integer(), nullable=False),
        sa.Column("box_count", sa.Integer(), nullable=False),
        sa.Column("published_by_sub", sa.Text(), nullable=False),
        sa.Column("published_by", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publish_audit_event_id", sa.Uuid(), nullable=False),
        sa.Column("reopened_by_sub", sa.Text(), nullable=True),
        sa.Column("reopened_by", sa.Text(), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column("reopen_audit_event_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "bump in ('major', 'minor', 'patch')", name=op.f("ck_package_versions_bump_known")
        ),
        sa.CheckConstraint(
            "(reopened_at is null) = (reopened_by_sub is null) "
            "and (reopened_at is null) = (reopened_by is null) "
            "and (reopened_at is null) = (reopen_reason is null) "
            "and (reopened_at is null) = (reopen_audit_event_id is null)",
            name=op.f("ck_package_versions_reopening_whole"),
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["packages.id"],
            name=op.f("fk_package_versions_package_id_packages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_id"],
            ["baselines.id"],
            name=op.f("fk_package_versions_baseline_id_baselines"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_versions")),
    )
    op.create_index(
        "uq_package_versions_package_version",
        "package_versions",
        ["package_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_package_versions_in_force",
        "package_versions",
        ["package_id"],
        unique=True,
        postgresql_where=sa.text("reopened_at IS NULL"),
    )
    op.add_column("baselines", sa.Column("scope_package_id", sa.Uuid(), nullable=True))
    op.add_column("baselines", sa.Column("scope_path", sa.Text(), nullable=True))

    op.execute(_FROZEN_ANCESTOR)
    op.execute(_ASSERT_UNFROZEN)
    for function in (_ELEMENTS, _DIAGRAMS, _PACKAGES, _RELATIONSHIPS, _OF_ELEMENT, _OF_DIAGRAM):
        op.execute(function)
    for table, function in _GUARDED:
        op.execute(
            f"CREATE TRIGGER {table}_frozen BEFORE INSERT OR UPDATE OR DELETE ON {table} "  # nosec B608
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    op.execute(_VERSIONS_IMMUTABLE)
    op.execute(
        "CREATE TRIGGER package_versions_append_only BEFORE UPDATE OR DELETE ON package_versions "
        "FOR EACH ROW EXECUTE FUNCTION ea_package_versions_append_only()"
    )
    op.execute(
        "CREATE TRIGGER package_versions_no_truncate BEFORE TRUNCATE ON package_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION ea_package_versions_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER package_versions_no_truncate ON package_versions")
    op.execute("DROP TRIGGER package_versions_append_only ON package_versions")
    for table, _ in reversed(_GUARDED):
        op.execute(f"DROP TRIGGER {table}_frozen ON {table}")  # nosec B608
    op.execute("DROP FUNCTION ea_package_versions_append_only()")
    for name in (
        "ea_guard_of_diagram",
        "ea_guard_of_element",
        "ea_guard_relationships",
        "ea_guard_packages",
        "ea_guard_diagrams",
        "ea_guard_elements",
    ):
        op.execute(f"DROP FUNCTION {name}()")  # nosec B608
    op.execute("DROP FUNCTION ea_assert_unfrozen(uuid, text, text)")
    op.execute("DROP FUNCTION ea_frozen_ancestor(uuid)")
    op.drop_column("baselines", "scope_path")
    op.drop_column("baselines", "scope_package_id")
    op.drop_index("uq_package_versions_in_force", table_name="package_versions")
    op.drop_index("uq_package_versions_package_version", table_name="package_versions")
    op.drop_table("package_versions")
