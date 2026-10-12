"""What the audit trail is made of, without a database (docs/adr/0046)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.audit import (
    AuditAction,
    EntityKind,
    FieldChange,
    aware,
    decode_cursor,
    diff_fields,
    document_view,
    element_view,
    encode_cursor,
    events_for,
    scene_views,
)
from ea.domain.diagram_objects import DiagramObject, DiagramScene, ObjectKind
from ea.domain.diagrams import DiagramNode
from ea.domain.documents import Document
from ea.domain.errors import InvalidCursorError
from ea.domain.model import Element

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(**overrides: object) -> Element:
    return Element.create(
        element_type=E.APPLICATION_COMPONENT,
        name=str(overrides.pop("name", "Billing")),
        now=NOW,
        **overrides,  # type: ignore[arg-type]
    )


class TestDiffFields:
    def test_it_lists_only_what_changed_sorted_by_field(self) -> None:
        before = {"name": "A", "description": "x", "layer": "application"}
        after = {"name": "B", "description": "x", "layer": "application"}

        assert diff_fields(before, after) == (FieldChange("name", "A", "B"),)

    def test_it_walks_into_properties_so_one_attribute_is_one_change(self) -> None:
        before = {"properties": {"owner": "ops", "tier": "1"}}
        after = {"properties": {"owner": "dev", "tier": "1", "cost": "9"}}

        assert diff_fields(before, after) == (
            FieldChange("properties.cost", None, "9"),
            FieldChange("properties.owner", "ops", "dev"),
        )

    def test_an_empty_map_filling_up_is_only_its_new_keys(self) -> None:
        assert diff_fields({"properties": {}}, {"properties": {"owner": "ops"}}) == (
            FieldChange("properties.owner", None, "ops"),
        )

    def test_a_creation_has_everything_as_new_and_a_deletion_as_gone(self) -> None:
        assert diff_fields(None, {"name": "A"}) == (FieldChange("name", None, "A"),)
        assert diff_fields({"name": "A"}, None) == (FieldChange("name", "A", None),)

    def test_it_can_leave_noise_out(self) -> None:
        changes = diff_fields(
            {"updated_at": "1", "name": "A"},
            {"updated_at": "2", "name": "A"},
            ignore=frozenset({"updated_at"}),
        )

        assert changes == ()


class TestTheViews:
    def test_an_element_view_is_the_read_representation_and_json_safe(self) -> None:
        element = an_element(properties={"owner": "ops"})

        view = element_view(element)

        assert view["id"] == str(element.id)
        assert view["element_type"] == "application_component"
        assert view["layer"] == "application"
        assert view["properties"] == {"owner": "ops"}
        assert view["created_at"] == NOW.isoformat()

    def test_an_element_view_names_its_package_or_none(self) -> None:
        package = uuid4()

        assert element_view(an_element())["package_id"] is None
        filed = replace(an_element(), package_id=package)
        assert element_view(filed)["package_id"] == str(package)

    def test_a_document_view_keeps_size_and_digest_and_never_the_text(self) -> None:
        document = Document.create(
            element_id=uuid4(), filename="runbook.md", content="# Secret plan\n", now=NOW
        )

        view = document_view(document)

        assert "Secret" not in str(view)
        assert view["byte_size"] == len(b"# Secret plan\n")
        assert len(view["sha256"]) == 64

    def test_a_scene_view_counts_what_moved_and_carries_no_coordinate_nor_text(self) -> None:
        first, second, third = uuid4(), uuid4(), uuid4()
        before = DiagramScene(nodes=(DiagramNode(first, 1, 2), DiagramNode(second, 3, 4)))
        after = DiagramScene(
            nodes=(DiagramNode(first, 9, 9), DiagramNode(third, 0, 0)),
            objects=(DiagramObject(uuid4(), ObjectKind.NOTE, 0, 0, 10, 10, text="secret plan"),),
        )

        old, new = scene_views(before, after, revision=4)

        assert old == {"diagram_revision": 4, "nodes": 2, "objects": 0, "edges": 0}
        assert new["diagram_revision"] == 5
        assert (new["nodes_added"], new["nodes_removed"], new["nodes_moved"]) == (1, 1, 1)
        assert "secret" not in str(new)


class TestActions:
    def test_an_action_knows_the_kind_of_entity_it_is_about(self) -> None:
        assert AuditAction.ELEMENT_CREATED.kind is EntityKind.ELEMENT
        assert AuditAction.DOCUMENT_ATTACHED.kind is EntityKind.DOCUMENT
        assert AuditAction.DIAGRAM_LAYOUT_REPLACED.kind is EntityKind.DIAGRAM


class TestTheCursor:
    def test_it_round_trips(self) -> None:
        event_id = uuid4()

        assert decode_cursor(encode_cursor(NOW, event_id)) == (NOW, event_id)

    @pytest.mark.parametrize("forged", ["", "not-base64!", "e30", "WyJ4Il0", "W10"])
    def test_a_forged_cursor_is_a_refusal_not_a_crash(self, forged: str) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor(forged)

    def test_a_naive_moment_is_read_as_utc(self) -> None:
        assert aware(datetime(2026, 1, 1, 0, 0)) == datetime(2026, 1, 1, tzinfo=UTC)
        assert aware(None) is None


class TestEventsFor:
    def test_ready_made_events_are_returned_as_they_are(self) -> None:
        events = ()

        assert events_for(events, "any row") is events

    def test_a_function_is_asked_with_the_row_the_write_replaces(self) -> None:
        asked: list[str] = []

        def build(before: str) -> tuple[()]:
            asked.append(before)
            return ()

        assert events_for(build, "the stored row") == ()
        assert asked == ["the stored row"]


class TestThePackageAndSavedQueryVocabulary:
    def test_their_actions_know_their_kind(self) -> None:
        assert AuditAction.PACKAGE_ELEMENTS_MOVED.kind is EntityKind.PACKAGE
        assert AuditAction.PACKAGE_DELETED.kind is EntityKind.PACKAGE
        assert AuditAction.SAVED_QUERY_UPDATED.kind is EntityKind.SAVED_QUERY

    def test_a_package_view_is_its_plain_columns(self) -> None:
        from ea.domain.audit import package_view
        from ea.domain.packages import Package

        parent = uuid4()
        package = Package.create(name="Budget", parent_id=parent, now=NOW, description="d")

        assert package_view(package) == {
            "id": str(package.id),
            "parent_id": str(parent),
            "name": "Budget",
            "description": "d",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }

    def test_a_saved_query_view_keeps_the_stored_json_and_the_author(self) -> None:
        from ea.domain.audit import saved_query_view
        from ea.domain.query import ElementQuery, to_json
        from ea.domain.saved_queries import SavedQuery

        saved = SavedQuery.create(name="n", query=ElementQuery(), created_by="alice", now=NOW)

        view = saved_query_view(saved)

        assert view["query"] == to_json(ElementQuery())
        assert view["created_by"] == "alice"

    def test_a_listing_of_ids_is_bounded_and_says_how_many_there_were(self) -> None:
        from ea.domain.audit import AUDIT_LISTED_IDS, listed

        ids = [uuid4() for _ in range(AUDIT_LISTED_IDS + 5)]

        shown = listed(ids)

        assert shown["count"] == AUDIT_LISTED_IDS + 5
        assert len(shown["ids"]) == AUDIT_LISTED_IDS


def test_an_audit_view_of_an_element_carries_its_review_state() -> None:
    view = element_view(
        Element.create(element_type=E.NODE, name="a", now=datetime(2026, 10, 10, tzinfo=UTC))
    )

    assert view["approval_state"] == "draft" and view["locked"] is False
    assert view["approved_by_sub"] is None and view["approved_at"] is None
