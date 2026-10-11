"""What the audit trail is made of, without a database (docs/adr/0044)."""

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
    layout_view,
)
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

    def test_a_layout_view_is_ordered_so_two_equal_layouts_compare_equal(self) -> None:
        first, second = uuid4(), uuid4()
        nodes = [DiagramNode(second, 1, 2), DiagramNode(first, 3, 4)]

        assert layout_view(nodes) == layout_view(list(reversed(nodes)))


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
