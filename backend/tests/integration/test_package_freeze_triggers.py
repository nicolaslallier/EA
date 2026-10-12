"""The database refuses every write into a published subtree (docs/adr/0074)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
FROZEN = "EA001"
RESTRICT = "23001"
CHECK = "23514"
UNIQUE = "23505"
FOREIGN_KEY = "23503"


def sqlstate_of(error: DBAPIError) -> str | None:
    return getattr(error.orig.__cause__, "sqlstate", None)  # type: ignore[union-attr]


def detail_of(error: DBAPIError) -> str | None:
    return getattr(error.orig.__cause__, "detail", None)  # type: ignore[union-attr]


async def insert(connection: AsyncConnection, table: str, **values: Any) -> None:
    columns = ", ".join(values)
    marks = ", ".join(f":{name}" for name in values)
    # The table and column names are the test's own constants, the values are bound.
    await connection.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({marks})"),  # nosec B608
        values,
    )


async def an_element(connection: AsyncConnection, name: str, package: UUID | None) -> UUID:
    element_id = uuid4()
    await insert(
        connection,
        "elements",
        id=element_id,
        element_type="node",
        layer="technology",
        aspect="active_structure",
        name=name,
        package_id=package,
        created_at=AT,
        updated_at=AT,
    )
    return element_id


async def a_package(connection: AsyncConnection, name: str, parent: UUID | None) -> UUID:
    package_id = uuid4()
    await insert(
        connection,
        "packages",
        id=package_id,
        parent_id=parent,
        name=name,
        created_at=AT,
        updated_at=AT,
    )
    return package_id


async def a_diagram(connection: AsyncConnection, name: str, package: UUID | None) -> UUID:
    diagram_id = uuid4()
    await insert(
        connection,
        "diagrams",
        id=diagram_id,
        name=name,
        package_id=package,
        created_at=AT,
        updated_at=AT,
    )
    return diagram_id


async def publish(connection: AsyncConnection, package_id: UUID, version: str = "v1.0.0") -> UUID:
    baseline_id, version_id = uuid4(), uuid4()
    await insert(
        connection,
        "baselines",
        id=baseline_id,
        name=f"{package_id} {version}",
        created_by_sub="s",
        created_by="n",
        created_at=AT,
    )
    await insert(
        connection,
        "package_versions",
        id=version_id,
        package_id=package_id,
        version=version,
        bump="minor",
        notes="notes",
        baseline_id=baseline_id,
        element_count=0,
        relationship_count=0,
        box_count=0,
        published_by_sub="s",
        published_by="n",
        published_at=AT,
        publish_audit_event_id=uuid4(),
    )
    return version_id


class World:
    """A published `root`, its sub-package, and one row of every guarded table."""

    root: UUID
    child: UUID
    inside: UUID
    inside_peer: UUID
    outside: UUID
    link: UUID
    document: UUID
    diagram: UUID
    outside_diagram: UUID
    stereotype: UUID
    stereotype_seeded: UUID
    note: UUID
    drawn: UUID
    link2: UUID
    free: UUID


async def build_world(connection: AsyncConnection) -> World:
    world = World()
    world.root = await a_package(connection, "Finance", None)
    world.free = await a_package(connection, "Libre", None)
    world.child = await a_package(connection, "Budget", world.root)
    world.inside = await an_element(connection, "inside", world.child)
    world.inside_peer = await an_element(connection, "inside-peer", world.root)
    world.outside = await an_element(connection, "outside", None)
    world.link = uuid4()
    await insert(
        connection,
        "relationships",
        id=world.link,
        relationship_type="association",
        source_id=world.inside,
        target_id=world.outside,
        source_type="node",
        target_type="node",
        created_at=AT,
    )
    world.link2 = uuid4()
    await insert(
        connection,
        "relationships",
        id=world.link2,
        relationship_type="association",
        source_id=world.outside,
        target_id=world.outside,
        source_type="node",
        target_type="node",
        created_at=AT,
    )
    world.document = uuid4()
    await insert(
        connection,
        "element_documents",
        id=world.document,
        element_id=world.inside,
        filename="a.md",
        content="x",
        created_at=AT,
        updated_at=AT,
    )
    world.diagram = await a_diagram(connection, "Vue", world.child)
    await insert(
        connection,
        "diagram_nodes",
        diagram_id=world.diagram,
        element_id=world.outside,
        x=0,
        y=0,
    )
    world.drawn = await an_element(connection, "drawn", None)
    await insert(
        connection, "diagram_nodes", diagram_id=world.diagram, element_id=world.drawn, x=0, y=0
    )
    world.outside_diagram = await a_diagram(connection, "Libre", None)
    profile, world.stereotype = uuid4(), uuid4()
    await insert(connection, "profiles", id=profile, name="P", created_at=AT, updated_at=AT)
    await insert(
        connection,
        "profile_stereotypes",
        id=world.stereotype,
        profile_id=profile,
        name="S",
        base_type="node",
        icon_key="k",
    )
    world.stereotype_seeded = uuid4()
    await insert(
        connection,
        "profile_stereotypes",
        id=world.stereotype_seeded,
        profile_id=profile,
        name="S2",
        base_type="node",
        icon_key="k",
    )
    await insert(
        connection,
        "element_stereotypes",
        element_id=world.inside,
        stereotype_id=world.stereotype_seeded,
    )
    world.note = uuid4()
    await insert(
        connection,
        "diagram_objects",
        id=world.note,
        diagram_id=world.diagram,
        kind="note",
        x=0,
        y=0,
        width=10,
        height=10,
    )
    await insert(connection, "diagram_edges", diagram_id=world.diagram, relationship_id=world.link)
    return world


#: label -> (table, operation) the DETAIL must name.
EXPECTED: dict[str, tuple[str, str]] = {
    "element update": ("elements", "UPDATE"),
    "element approval": ("elements", "UPDATE"),
    "element delete": ("elements", "DELETE"),
    "element filed in": ("elements", "UPDATE"),
    "element filed out": ("elements", "UPDATE"),
    "element created inside": ("elements", "INSERT"),
    "link update (one end inside)": ("relationships", "UPDATE"),
    "link delete (one end inside)": ("relationships", "DELETE"),
    "link created (one end inside)": ("relationships", "INSERT"),
    "document update": ("element_documents", "UPDATE"),
    "document delete": ("element_documents", "DELETE"),
    "document created": ("element_documents", "INSERT"),
    "stereotype laid": ("element_stereotypes", "INSERT"),
    "stereotype link updated": ("element_stereotypes", "UPDATE"),
    "stereotype link deleted": ("element_stereotypes", "DELETE"),
    "diagram update": ("diagrams", "UPDATE"),
    "diagram delete": ("diagrams", "DELETE"),
    "diagram filed in": ("diagrams", "UPDATE"),
    "diagram box moved": ("diagram_nodes", "UPDATE"),
    "diagram box added": ("diagram_nodes", "INSERT"),
    "diagram note added": ("diagram_objects", "INSERT"),
    "diagram note moved": ("diagram_objects", "UPDATE"),
    "diagram note deleted": ("diagram_objects", "DELETE"),
    "diagram edge added": ("diagram_edges", "INSERT"),
    "diagram edge updated": ("diagram_edges", "UPDATE"),
    "diagram edge deleted": ("diagram_edges", "DELETE"),
    "package renamed": ("packages", "UPDATE"),
    "published package renamed": ("packages", "UPDATE"),
    "package moved in": ("packages", "UPDATE"),
    "sub-package created": ("packages", "INSERT"),
    "package deleted": ("packages", "DELETE"),
    "outside element deleted while a published diagram draws it": ("diagram_nodes", "DELETE"),
}


def refused(world: World) -> dict[str, tuple[str, dict[str, Any]]]:
    """One write per guarded table (and the tricky corners), each of which must be refused."""
    return {
        "element update": (
            "UPDATE elements SET description = 'x' WHERE id = :id",
            {"id": world.inside},
        ),
        "element approval": (
            "UPDATE elements SET approval_state = 'in_review' WHERE id = :id",
            {"id": world.inside},
        ),
        "element delete": ("DELETE FROM elements WHERE id = :id", {"id": world.inside}),
        "element filed in": (
            "UPDATE elements SET package_id = :p WHERE id = :id",
            {"id": world.outside, "p": world.child},
        ),
        "element filed out": (
            "UPDATE elements SET package_id = NULL WHERE id = :id",
            {"id": world.inside},
        ),
        "element created inside": (
            "INSERT INTO elements (id, element_type, layer, aspect, name, package_id, "
            "created_at, updated_at) VALUES (:id, 'node', 'technology', 'active_structure', "
            "'new', :p, :at, :at)",
            {"id": uuid4(), "p": world.child, "at": AT},
        ),
        "link update (one end inside)": (
            "UPDATE relationships SET name = 'x' WHERE id = :id",
            {"id": world.link},
        ),
        "link delete (one end inside)": (
            "DELETE FROM relationships WHERE id = :id",
            {"id": world.link},
        ),
        "link created (one end inside)": (
            "INSERT INTO relationships (id, relationship_type, source_id, target_id, "
            "source_type, target_type, created_at) VALUES "
            "(:id, 'association', :s, :t, 'node', 'node', :at)",
            {"id": uuid4(), "s": world.outside, "t": world.inside_peer, "at": AT},
        ),
        "document update": (
            "UPDATE element_documents SET content = 'y' WHERE id = :id",
            {"id": world.document},
        ),
        "document delete": (
            "DELETE FROM element_documents WHERE id = :id",
            {"id": world.document},
        ),
        "document created": (
            "INSERT INTO element_documents (id, element_id, filename, content, created_at, "
            "updated_at) VALUES (:id, :e, 'b.md', 'x', :at, :at)",
            {"id": uuid4(), "e": world.inside, "at": AT},
        ),
        "stereotype laid": (
            "INSERT INTO element_stereotypes (element_id, stereotype_id) VALUES (:e, :s)",
            {"e": world.inside, "s": world.stereotype},
        ),
        "diagram update": (
            "UPDATE diagrams SET description = 'x' WHERE id = :id",
            {"id": world.diagram},
        ),
        "diagram delete": ("DELETE FROM diagrams WHERE id = :id", {"id": world.diagram}),
        "diagram filed in": (
            "UPDATE diagrams SET package_id = :p WHERE id = :id",
            {"id": world.outside_diagram, "p": world.child},
        ),
        "diagram box moved": (
            "UPDATE diagram_nodes SET x = 9 WHERE diagram_id = :d",
            {"d": world.diagram},
        ),
        "diagram box added": (
            "INSERT INTO diagram_nodes (diagram_id, element_id, x, y) VALUES (:d, :e, 0, 0)",
            {"d": world.diagram, "e": world.inside_peer},
        ),
        "diagram note added": (
            "INSERT INTO diagram_objects (id, diagram_id, kind, x, y, width, height) "
            "VALUES (:id, :d, 'note', 0, 0, 10, 10)",
            {"id": uuid4(), "d": world.diagram},
        ),
        "package renamed": (
            "UPDATE packages SET name = 'X' WHERE id = :id",
            {"id": world.child},
        ),
        "published package renamed": (
            "UPDATE packages SET name = 'X' WHERE id = :id",
            {"id": world.root},
        ),
        "package moved in": (
            "UPDATE packages SET parent_id = :p WHERE id = :id",
            {"id": world.free, "p": world.child},
        ),
        "sub-package created": (
            "INSERT INTO packages (id, parent_id, name, created_at, updated_at) "
            "VALUES (:id, :p, 'N', :at, :at)",
            {"id": uuid4(), "p": world.child, "at": AT},
        ),
        "package deleted": ("DELETE FROM packages WHERE id = :id", {"id": world.child}),
        "stereotype link updated": (
            "UPDATE element_stereotypes SET stereotype_id = :s WHERE element_id = :e",
            {"e": world.inside, "s": world.stereotype},
        ),
        "stereotype link deleted": (
            "DELETE FROM element_stereotypes WHERE element_id = :e",
            {"e": world.inside},
        ),
        "diagram note moved": (
            "UPDATE diagram_objects SET x = 9 WHERE id = :id",
            {"id": world.note},
        ),
        "diagram note deleted": (
            "DELETE FROM diagram_objects WHERE id = :id",
            {"id": world.note},
        ),
        "diagram edge added": (
            "INSERT INTO diagram_edges (diagram_id, relationship_id) VALUES (:d, :r)",
            {"d": world.diagram, "r": world.link2},
        ),
        "diagram edge updated": (
            "UPDATE diagram_edges SET waypoints = '[[1,1]]' WHERE diagram_id = :d",
            {"d": world.diagram},
        ),
        "diagram edge deleted": (
            "DELETE FROM diagram_edges WHERE diagram_id = :d",
            {"d": world.diagram},
        ),
        # Review focus 5: an unpublished element that a published diagram draws.
        "outside element deleted while a published diagram draws it": (
            "DELETE FROM elements WHERE id = :id",
            {"id": world.drawn},
        ),
    }


async def test_every_write_into_a_published_subtree_is_refused_by_the_database(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        await publish(connection, world.root)

    for label, (sql, params) in refused(world).items():
        with pytest.raises(DBAPIError) as raised:
            async with engine_at_head.begin() as connection:
                await connection.execute(text(sql), params)
        assert sqlstate_of(raised.value) == FROZEN, label
        parts = (detail_of(raised.value) or "").split("|")
        assert parts[0] == str(world.root), label
        assert parts[1] == "v1.0.0", label
        assert (parts[2], parts[3]) == EXPECTED[label], (label, parts)

    async with engine_at_head.connect() as connection:
        untouched = (
            await connection.execute(
                text("SELECT description FROM elements WHERE id = :id"), {"id": world.inside}
            )
        ).scalar_one()
    assert untouched == ""


async def test_what_is_outside_a_published_subtree_stays_writable(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        await publish(connection, world.root)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET description = 'ok' WHERE id = :id"), {"id": world.outside}
        )
        await connection.execute(
            text("UPDATE diagrams SET description = 'ok' WHERE id = :id"),
            {"id": world.outside_diagram},
        )
        await a_package(connection, "Ailleurs", None)


async def test_nothing_is_guarded_while_nothing_is_published(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET description = 'ok' WHERE id = :id"), {"id": world.inside}
        )
        await connection.execute(text("DELETE FROM elements WHERE id = :id"), {"id": world.inside})


async def test_reopening_lifts_the_freeze(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        version_id = await publish(connection, world.root)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "UPDATE package_versions SET reopened_by_sub = 's', reopened_by = 'n', "
                "reopened_at = :at, reopen_reason = 'r', reopen_audit_event_id = :e "
                "WHERE id = :id"
            ),
            {"at": AT, "e": uuid4(), "id": version_id},
        )
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET description = 'ok' WHERE id = :id"), {"id": world.inside}
        )


async def test_a_version_is_append_only_and_only_its_reopening_can_be_written(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        version_id = await publish(connection, world.root)

    for sql in (
        "UPDATE package_versions SET notes = 'rewritten' WHERE id = :id",
        "UPDATE package_versions SET version = 'v9.9.9' WHERE id = :id",
        "UPDATE package_versions SET reopen_reason = 'x' WHERE id = :id",
        "UPDATE package_versions SET reopened_at = :at WHERE id = :id",
        "DELETE FROM package_versions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            async with engine_at_head.begin() as connection:
                await connection.execute(text(sql), {"id": version_id, "at": AT})
        assert sqlstate_of(raised.value) in {RESTRICT, CHECK}, sql
    with pytest.raises(DBAPIError) as raised:
        async with engine_at_head.begin() as connection:
            await connection.execute(text("TRUNCATE package_versions"))
    assert sqlstate_of(raised.value) == RESTRICT

    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "UPDATE package_versions SET reopened_by_sub = 's', reopened_by = 'n', "
                "reopened_at = :at, reopen_reason = 'r', reopen_audit_event_id = :e "
                "WHERE id = :id"
            ),
            {"at": AT, "e": uuid4(), "id": version_id},
        )
    with pytest.raises(DBAPIError) as raised:  # a reopened version never changes again
        async with engine_at_head.begin() as connection:
            await connection.execute(
                text("UPDATE package_versions SET reopen_reason = 'again' WHERE id = :id"),
                {"id": version_id},
            )
    assert sqlstate_of(raised.value) == RESTRICT


async def test_only_one_version_is_in_force_per_package(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await publish(connection, root, "v1.0.0")
    with pytest.raises(DBAPIError) as raised:
        async with engine_at_head.begin() as connection:
            await publish(connection, root, "v1.1.0")
    assert sqlstate_of(raised.value) == UNIQUE


async def test_a_baseline_a_version_points_at_cannot_be_deleted(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await publish(connection, root)
    with pytest.raises(DBAPIError) as raised:
        async with engine_at_head.begin() as connection:
            await connection.execute(text("DELETE FROM baselines"))
    assert sqlstate_of(raised.value) == FOREIGN_KEY


async def test_a_temp_table_cannot_shadow_the_guard(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        await publish(connection, world.root)
    with pytest.raises(DBAPIError) as raised:
        async with engine_at_head.begin() as connection:
            await connection.execute(
                text("CREATE TEMP TABLE package_versions (reopened_at timestamptz)")
            )
            await connection.execute(text("CREATE TEMP TABLE elements (id uuid, package_id uuid)"))
            await connection.execute(
                text("UPDATE public.elements SET description = 'x' WHERE id = :id"),
                {"id": world.inside},
            )
    assert sqlstate_of(raised.value) == FROZEN


CLOSE_ALL = (
    "UPDATE package_versions SET reopened_by_sub = 's', reopened_by = 'n', "
    "reopened_at = :at, reopen_reason = 'r', reopen_audit_event_id = :e WHERE id = :id"
)


async def test_closing_a_version_sets_the_five_columns_together(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        version_id = await publish(connection, root)
    for sql in (
        "UPDATE package_versions SET reopened_at = :at WHERE id = :id",
        "UPDATE package_versions SET reopened_at = :at, reopened_by_sub = 's' WHERE id = :id",
        "UPDATE package_versions SET reopened_at = :at, reopened_by_sub = 's', reopened_by = 'n', "
        "reopen_reason = 'r' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            async with engine_at_head.begin() as connection:
                await connection.execute(text(sql), {"id": version_id, "at": AT})
        assert sqlstate_of(raised.value) == CHECK, sql
    with pytest.raises(DBAPIError) as raised:  # an update that leaves it in force
        async with engine_at_head.begin() as connection:
            await connection.execute(
                text("UPDATE package_versions SET reopen_reason = 'r' WHERE id = :id"),
                {"id": version_id},
            )
    assert sqlstate_of(raised.value) == RESTRICT
    async with engine_at_head.begin() as connection:
        await connection.execute(text(CLOSE_ALL), {"at": AT, "e": uuid4(), "id": version_id})
