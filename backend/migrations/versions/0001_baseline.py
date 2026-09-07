"""baseline: the empty root of the relational history

Revision ID: 0001
Revises:
Date: 2026-09-07

This migration creates nothing, on purpose. PostgreSQL holds no table yet
(docs/adr/0015); what the chain needs today is a root, so that the first real
migration has a `down_revision` to name and `alembic upgrade head` on a fresh
database is a meaningful command — it creates `alembic_version` and stamps it.

Deleting this file later would orphan every revision built on it. The first
table adds a revision after it; it does not replace it.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
