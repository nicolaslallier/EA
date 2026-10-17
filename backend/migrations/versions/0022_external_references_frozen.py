"""une référence externe est gelée avec le sous-arbre publié de son élément (docs/adr/0076)

Revision ID: 0022
Revises: 0021
Date: 2026-10-17

Un trigger de plus, `external_references_frozen`, sur la fonction `ea_guard_of_element` que la
`0020` a créée pour les documents et les stéréotypes : le package de l'élément décide. Lier
(`INSERT`) et délier (`DELETE`) sont refusés sous un package publié, et un `UPDATE` seulement
s'il touche à ce que la référence *est* (`element_id`, `kind`, `target`, `external_id`, `url`) :
le rafraîchissement du cache (titre, état, `synced_at`, `attempted_at`) est une donnée du tiers,
le geler ferait échouer pour toujours le rafraîchissement d'un élément publié.

Aucune ligne n'est lue ni réécrite : la révision ne peut pas échouer sur des données déjà là.
`lock_timeout` borne l'attente du verrou que prend la création du trigger. `downgrade` le retire.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "10s"

#: What a reference *is*; an UPDATE of anything else (the cached copy) is not a write to the model.
_IDENTITY = ("element_id", "kind", "target", "external_id", "url")

#: (table, trigger function), as in 0020 — `tests/unit/test_frozen_tables.py` reads both.
_GUARDED = (("external_references", "ea_guard_of_element"),)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")  # nosec B608 - a constant
    for table, function in _GUARDED:
        op.execute(
            f"CREATE TRIGGER {table}_frozen "  # nosec B608 - constants
            f"BEFORE INSERT OR DELETE OR UPDATE OF {', '.join(_IDENTITY)} ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")  # nosec B608 - a constant
    for table, _ in reversed(_GUARDED):
        op.execute(f"DROP TRIGGER {table}_frozen ON {table}")  # nosec B608 - constants
