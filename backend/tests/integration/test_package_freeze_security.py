"""One test per line of the threat model (spec 2026-10-12-gel-par-package-design.md §10).

- Editor publishes or reopens ................ tests/unit/test_package_publication_service.py
                                               tests/e2e/test_package_publication_api.py (401/403)
- Forgotten door ............................. tests/integration/test_package_freeze_triggers.py
- Race with a publication .................... tests/integration/test_package_publication.py
- Filing in and out of a frozen subtree ...... tests/integration/test_package_freeze_triggers.py
- Reopening leaves no trace .................. below
- A write the trigger cannot judge ........... below (fail closed)
- Injection through notes / adr / reason ..... below
- Leak in logs and errors .................... below
- Flooding the journal ....................... tests/unit/test_frozen_refusal_recorder.py
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import work
from ea.domain.errors import PackageFrozenError
from ea.domain.package_versions import Bump
from ea.repositories.package_store import PostgresPackageRepository
from tests.integration.test_package_freeze_triggers import a_package, an_element, publish
from tests.integration.test_package_publication import publish_it, reopen_it

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def _repo(engine: AsyncEngine) -> PostgresPackageRepository:
    return PostgresPackageRepository(async_sessionmaker(engine, expire_on_commit=False))


async def test_reopening_always_leaves_a_closed_row_an_event_and_a_reason(
    engine_at_head: AsyncEngine,
) -> None:
    repo = _repo(engine_at_head)
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    published = await publish_it(repo, root)
    closed = await reopen_it(repo, root)
    async with engine_at_head.connect() as connection:
        actions = [
            row[0]
            for row in await connection.execute(
                text("SELECT action FROM audit_events WHERE entity_id = :p ORDER BY action"),
                {"p": root},
            )
        ]
        stored = (
            await connection.execute(
                text("SELECT reopen_reason, reopen_audit_event_id FROM package_versions")
            )
        ).one()
    assert actions == ["package.published", "package.reopened"]  # both events share one clock
    assert tuple(stored) == ("coquille", closed.reopen_audit_event_id)
    assert published.publish_audit_event_id != closed.reopen_audit_event_id


async def test_the_audit_trail_keeps_digests_and_never_the_words(
    engine_at_head: AsyncEngine,
) -> None:
    repo = _repo(engine_at_head)
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    await publish_it(repo, root, notes="NOTE-SECRÈTE-123", adr="ADR-SECRET-9", bump=Bump.MAJOR)
    async with engine_at_head.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT after::text FROM audit_events WHERE entity_id = :p"), {"p": root}
            )
        ).scalar_one()
    assert "NOTE-SECRÈTE-123" not in stored
    assert "ADR-SECRET-9" not in stored
    assert "notes_sha256" in stored


async def test_a_notes_text_made_of_sql_and_markup_is_stored_as_text(
    engine_at_head: AsyncEngine,
) -> None:
    repo = _repo(engine_at_head)
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await an_element(connection, "bystander", None)
    hostile = "'); DROP TABLE elements; --\n<script>alert(1)</script>${jndi:x}"
    version = await publish_it(repo, root, notes=hostile, adr=None, bump=Bump.MINOR)
    assert version.notes == hostile
    async with engine_at_head.connect() as connection:
        assert (await connection.execute(text("SELECT count(*) FROM elements"))).scalar_one() == 1


async def test_a_refusal_never_names_the_element_or_its_content(
    engine_at_head: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance-Secret", None)
        element = await an_element(connection, "NOM-D-ELEMENT-SECRET", root)
        await publish(connection, root)
    sessions = async_sessionmaker(engine_at_head)
    with caplog.at_level(logging.DEBUG), pytest.raises(PackageFrozenError) as raised:
        async with work(sessions) as session:
            await session.execute(
                text("UPDATE elements SET description = 'TEXTE-SECRET' WHERE id = :id"),
                {"id": element},
            )
    shown = str(raised.value) + " " + caplog.text
    for secret in ("NOM-D-ELEMENT-SECRET", "TEXTE-SECRET", "Finance-Secret"):
        assert secret not in shown


async def test_a_trigger_that_cannot_decide_refuses_the_write(engine_at_head: AsyncEngine) -> None:
    """Fail closed: if the freeze lookup itself breaks, the write does not go through."""
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root)
    async with engine_at_head.begin() as connection:
        await connection.execute(text("ALTER FUNCTION ea_frozen_ancestor(uuid) RENAME TO gone"))
    try:
        with pytest.raises(DBAPIError):
            async with engine_at_head.begin() as connection:
                await connection.execute(
                    text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": element}
                )
    finally:
        async with engine_at_head.begin() as connection:
            await connection.execute(text("ALTER FUNCTION gone(uuid) RENAME TO ea_frozen_ancestor"))


async def test_no_table_that_hangs_from_the_model_is_left_unguarded(
    engine_at_head: AsyncEngine,
) -> None:
    """A new table that references elements, diagrams or packages must get its trigger, or be
    listed here with the reason it is not frozen."""
    exempt = {
        "baseline_elements": "a copy that outlives the model",
        "baseline_relationships": "a copy that outlives the model",
        "baseline_boxes": "a copy that outlives the model",
        "document_chunks": "the derived search index: a reindex must still run",
        "external_references": "third-party cache, exempt by the spec (title and state only)",
        "audit_events": "the trail itself",
        "package_versions": "append-only by its own triggers",
        "diagram_templates": "carry no element and no package",
        "saved_queries": (
            "a published subtree holds no live view (publication refuses it), so a query edit "
            "cannot change what it shows; a frozen view stores its boxes"
        ),
        "file_metadata": "files of the bucket",
    }
    async with engine_at_head.connect() as connection:
        referencing = {
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT DISTINCT c.conrelid::regclass::text FROM pg_constraint c "
                    "WHERE c.contype = 'f' AND c.confrelid::regclass::text IN "
                    "('elements', 'diagrams', 'packages', 'relationships')"
                )
            )
        }
        guarded = {
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT DISTINCT c.relname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE t.tgname LIKE '%\\_frozen' AND NOT t.tgisinternal"
                )
            )
        }
    model = {"elements", "diagrams", "packages", "relationships"}
    missing = referencing - guarded - set(exempt) - model
    assert missing == set(), f"unguarded tables: {sorted(missing)}"
